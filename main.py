from fastapi import FastAPI, HTTPException, Form, Depends ,Query
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, Column, Integer, String, Date 
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from jose import jwt
from datetime import datetime, timedelta ,date
from typing import Optional ,List
import csv, io, requests, os, json
from dotenv import load_dotenv
import gspread
from oauth2client.service_account import ServiceAccountCredentials

load_dotenv()

# ----------------- Load Google Credentials from ENV -----------------
def safe_get_env(key: str) -> str:
    value = os.getenv(key)
    if value is None:
        raise RuntimeError(f"Missing environment variable: {key}")
    return value

google_creds = {
    "type": safe_get_env("GOOGLE_TYPE"),
    "project_id": safe_get_env("GOOGLE_PROJECT_ID"),
    "private_key_id": safe_get_env("GOOGLE_PRIVATE_KEY_ID"),
    "private_key": safe_get_env("GOOGLE_PRIVATE_KEY").replace("\\n", "\n"),
    "client_email": safe_get_env("GOOGLE_CLIENT_EMAIL"),
    "client_id": safe_get_env("GOOGLE_CLIENT_ID"),
    "auth_uri": safe_get_env("GOOGLE_AUTH_URI"),
    "token_uri": safe_get_env("GOOGLE_TOKEN_URI"),
    "auth_provider_x509_cert_url": safe_get_env("GOOGLE_AUTH_PROVIDER_CERT_URL"),
    "client_x509_cert_url": safe_get_env("GOOGLE_CLIENT_CERT_URL"),
    "universe_domain": safe_get_env("GOOGLE_UNIVERSE_DOMAIN")
}

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
credentials: ServiceAccountCredentials = ServiceAccountCredentials.from_json_keyfile_dict(google_creds, scope)
gc = gspread.authorize(credentials)

# ----------------- Google Sheets Setup -----------------
SHEET_ID = "1WX44a8gOrTPs4nmqjfSCAwn99QtFBiy72JeyLDNquMQ"
GID_MAP = {
    "cancellations": "1689593326",
    "upcoming": "1840935840",
    "past":"304133303",
    "schedule": "1739684591",
    "standby":"2043157366",
    "sheet1": "0"
}

sheet = gc.open_by_key(SHEET_ID).worksheet("Sheet1")

def get_csv_url(sheet_name: str) -> str:
    gid = GID_MAP.get(sheet_name.lower())
    if not gid:
        raise ValueError(f"Sheet name '{sheet_name}' not found.")
    return f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={gid}"

# ----------------- App & DB Setup -----------------
app = FastAPI(title="WhatsApp Admin Panel")

SQLITE_DB_URL = "sqlite:///./whatsapp_db.sqlite"
SECRET_KEY = "your-secret-key"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

engine = create_engine(SQLITE_DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ----------------- Models -----------------
class Admin(Base):
    __tablename__ = "admins"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False, unique=True)
    password = Column(String, nullable=False)

class User(Base):
    __tablename__ = "User"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    email = Column(String,unique=True, index=True)  # ✅ New email field
    contact = Column(String)
    outlet_role = Column(String)
    role = Column(String)
    user_status = Column(String)

class Booking(Base):
    __tablename__ = "bookings"
    id = Column(Integer, primary_key=True, index=True)
    worker_name = Column(String)
    status = Column(String)
    date = Column(Date)
    start_time = Column(String)
    end_time = Column(String)
    outlet = Column(String)
    flags = Column(String, default="")

class StandbyWorker(Base):
    __tablename__ = "standby_workers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    contact = Column(String)
    roles = Column(String)  # comma-separated roles
    outlet = Column(String)
    status = Column(String)  # e.g., Available, Low reliability, Confirmed
    days_available = Column(Integer)
    availability_date = Column(Date)
Base.metadata.create_all(bind=engine)

# ----------------- Helpers -----------------
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ----------------- Startup -----------------
@app.on_event("startup")
def create_default_admin():
    with SessionLocal() as db:
        if not db.query(Admin).filter(Admin.email == "admin@gmail.com").first():
            db.add(Admin(email="admin@gmail.com", password="admin123"))
            db.commit()

# ----------------- Routes -----------------
@app.post("/Login")
async def login(email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    admin = db.query(Admin).filter(Admin.email == email, Admin.password == password).first()
    if not admin:
        return{"status":"False", "message":"Invalid credentials"}
    token = create_access_token({"sub": admin.email})
    return JSONResponse(content={"status":"True","message": "Login successful", "access_token": token, "token_type": "bearer"})

@app.post("/add_new_users")
def create_user(
    name: str = Form(...),
    email: str = Form(...),  
    contact: str = Form(...),
    outlet_role: str = Form(...),
    role: str = Form(...),
    user_status: str = Form(...),
    db: Session = Depends(get_db)
):
    user = User(
        name=name,
        email=email,
        contact=contact,
        outlet_role=outlet_role,
        role=role,
        user_status=user_status
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    sheet.append_row([
        str(user.id), user.name, user.email, user.contact, user.outlet_role, user.role, user.user_status
    ])
    return {"status": "True", "message": "User added", "results": user}

# Update user API
@app.put("/update_user/{id}")
def update_user(
    id: int,
    name: str = Form(...),
    email: str = Form(...),  # ✅ New parameter
    contact: str = Form(...),
    outlet_role: str = Form(...),
    role: str = Form(...),
    user_status: str = Form(...),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == id).first()
    if not user:
        return {"status": "False", "message": "User not found"}

    user.name = name
    user.email = email
    user.contact = contact
    user.outlet_role = outlet_role
    user.role = role
    user.user_status = user_status
    db.commit()
    db.refresh(user)

    all_data = sheet.get_all_records()
    for idx, row in enumerate(all_data, start=2):
        if str(row.get("id")) == str(user.id):
            sheet.update(f"A{idx}:G{idx}", [[
                str(user.id), user.name, user.email, user.contact,
                user.outlet_role, user.role, user.user_status
            ]])
            break
    return {"status": "True", "message": "User updated", "results": user}

# Delete user by ID
@app.delete("/delete_user/{id}")
def delete_user(id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == id).first()
    if not user:
        return {"status": "False","message":"User not found"}
    db.delete(user)
    db.commit()
    return {"status": "True", "message": f"User '{id}' deleted"}

@app.get("/api/users")
def get_users():
    try:
        url = get_csv_url("sheet1")
        response = requests.get(url)
        response.raise_for_status()
        reader = csv.DictReader(io.StringIO(response.text))
        users = [{
            "name": row.get("Name", ""),
            "email": row.get("Email", ""),
            "contact": row.get("Worker Phone", ""),
            "outlet_type": row.get("Outlet", ""),
            "roles": row.get("Roles", "").strip() if row.get("Roles", "").strip() else "",
            "user_status": row.get("Status", ""),
            "availability_days": row.get("Availability", "")
        } for row in reader]
        return {"status":"True","message": "Users fetched", "results": users}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/upcoming bookings/{sheet_name}")
def fetch_google_sheet(sheet_name: str):
    try:
        url = get_csv_url(sheet_name)
        response = requests.get(url)
        response.raise_for_status()
        reader = csv.DictReader(io.StringIO(response.text))
        return {"sheet": sheet_name, "data": list(reader)}
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid sheet name")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

sheet = gc.open_by_key(SHEET_ID).worksheet("standby")

def get_csv_url(sheet_name: str) -> str:
    gid = GID_MAP.get(sheet_name.lower())
    if not gid:
        raise ValueError(f"Sheet name '{sheet_name}' not found.")
    return f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={gid}"

@app.post("/standby/add")
def add_to_standby_form( name: str = Form(...), contact: str = Form(...),roles: str = Form(...),
    outlet: str = Form(...),status: str = Form(...),days_available: int = Form(...),availability_date: str = Form(...),
    db: Session = Depends(get_db)):
    try:
        parsed_date = datetime.strptime(availability_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    # Add to database
    worker = StandbyWorker( name=name,  contact=contact, roles=roles,  outlet=outlet, status=status,
        days_available=days_available, availability_date=parsed_date )
    db.add(worker)
    db.commit()
    db.refresh(worker)
    # Add to Google Sheet
    try:
        sheet = get_google_sheet()
        sheet.append_row([ name, contact,  roles,  outlet,  status, days_available, parsed_date.isoformat() ])
    except Exception as e:
        print(f"Failed to write to Google Sheet: {e}")
    return {"status": "True", "message": "Worker added to standby", "results": worker}
# ----------------- Run Uvicorn -----------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
