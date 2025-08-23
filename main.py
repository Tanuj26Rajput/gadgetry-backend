from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Depends, Header
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List, Optional
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime, timezone, timedelta
from agent import workflow, agentstate
import traceback
import bcrypt
from jose import JWTError, jwt
import os
import requests
from uuid import uuid4
import secrets
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
import asyncio
import random

load_dotenv()

#JWT config
JWT_SECRET=os.getenv("SECRET_KEY")
JWT_ALGORITHM=os.getenv("JWT_ALGORITHM")
JWT_EXPIRY_SECONDS=3600

#MongoDB setup
MONGO_URI=os.getenv("MONGO_URI")
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["gadgetry"]
session_collection = db["sessions"]
user_collection = db['users']

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://findmygadget.shop",
        "https://www.findmygadget.shop"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

conf = ConnectionConfig(
    MAIL_USERNAME = os.getenv("EMAIL_USER"),
    MAIL_PASSWORD = os.getenv("EMAIL_PASS"),
    MAIL_FROM = os.getenv("EMAIL_USER"),
    MAIL_PORT = 587,
    MAIL_SERVER = "smtp.gmail.com",
    MAIL_STARTTLS = True,
    MAIL_SSL_TLS = False,
    USE_CREDENTIALS = True
)

class QueryRequest(BaseModel):
    query: str
    session_id: Optional[str] = "default"

class QueryResponse(BaseModel):
    recommendation: str
    product_list: List[dict]

class UserCreate(BaseModel):
    name: str
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str

class VerifyOTPRequest(BaseModel):
    email: str
    otp: str

class ResendOTPRequest(BaseModel):
    email: str

class VerifyResetOTPRequest(BaseModel):
    email: str
    otp: str

class ResetPassword(BaseModel):
    email: str
    otp: str
    new_password: str

def decode_token(token: str):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    
@app.get("/")
def root():
    return {"message": "Smart gadget assistant is running✅"}

@app.post("/gadget-assist")
def gadget_assist(request: QueryRequest, authorization: Optional[str] = Header(None)):
    try:
        query = request.query
        session_id = request.session_id

        user_email = None
        if authorization and authorization.startswith("Bearer "):
            token = authorization.split(" ")[1]
            payload = decode_token(token)
            user_email = payload.get("email")

        if not user_email:
            return JSONResponse(status_code=403, content={"error": "Authentication required"})
        
        user_doc = user_collection.find_one({"email": user_email})
        if not user_doc:
            return JSONResponse(status_code=403, content={"error": "User not found"})
        
        if not user_doc.get("is_verified", False):
            return JSONResponse(status_code=403, content={"error": "Email not verified. Please verify your account before chatting"})
        
        composite_session_id = f"{user_email}__{session_id}"

        session = session_collection.find_one({"session_id": composite_session_id})

        if not session:
            session_state: agentstate = {
                "session_id": composite_session_id,
                "query": "",
                "budget": 0,
                "category": "",
                "product": "",
                "product_list": [],
                "recommendation": "",
                "user_email": user_email,
                "createdate": datetime.now(timezone.utc)
            }
        else:
            session.pop("_id", None)
            session_state: agentstate = session
            session_state["user_email"] = user_email

        session_state['query'] = query
        result = workflow.invoke(session_state)
        
        result['session_id'] = composite_session_id
        result["user_email"] = user_email
        result['createdate'] = datetime.now(timezone.utc)
        
        session_collection.update_one(
            {"session_id": composite_session_id},
            {"$set": result},
            upsert=True
        )

        return{
            "recommendation": result['recommendation'],
            "product_list": result["product_list"],
            "session_id": composite_session_id
        }
    
    except Exception as e:
        print("🔥 ERROR in /gadget-assist:", traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "details": str(e)},
        )
    
def create_token(email: str):
    payload = {
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=JWT_EXPIRY_SECONDS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def send_otp_email(email: str, otp: str):
    message = MessageSchema(
        subject="Verify your FindMyGadget Account",
        recipients=[email],
        body=f"""
            <p>Hi!</p>
            <p>Your OTP code is: <b>{otp}</b></p>
            <p>This code will expire in 10 minutes.</p>
        """,
        subtype="html"
    )
    fm = FastMail(conf)
    await fm.send_message(message)

@app.post("/signup")
async def signup(user: UserCreate):
    if user_collection.find_one({"email": user.email}):
        raise HTTPException(status_code=400, detail="Email already exist")
    
    hashed_pw = bcrypt.hashpw(user.password.encode('utf-8'), bcrypt.gensalt())

    otp = str(random.randint(100000, 999999))
    otp_expiry = datetime.now(timezone.utc) + timedelta(minutes=10)

    user_collection.insert_one({
        "name": user.name,
        "email": user.email,
        "password": hashed_pw,
        "created_at": datetime.now(timezone.utc),
        "otp": otp,
        "otp_expiry": otp_expiry,
        "is_verified": False
    })

    await send_otp_email(user.email, otp)
    return {"msg": "Signup successfull, please verify your email before logging in."}

@app.post("/verify-otp")
def verify_otp(data: VerifyOTPRequest):
    try:
        user = user_collection.find_one({"email": data.email})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        if user.get("is_verified"):
            return {"success": True, "message": "Email already verified"}

        if user.get("otp") != data.otp:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid OTP"})

        expiry = user.get("otp_expiry")
        if expiry:
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)

            if expiry < datetime.now(timezone.utc):
                return JSONResponse(status_code=400, content={"success": False, "error": "OTP expired"})

        user_collection.update_one(
            {"_id": user["_id"]},
            {"$set": {"is_verified": True, "verified_at": datetime.now(timezone.utc)},
             "$unset": {"otp": "", "otp_expiry": ""}}
        )
        return {"success": True, "message": "Email verified successfully"}
    except Exception:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"success": False, "error": "Internal server error"})

@app.post("/resend-otp")
async def resend_otp(data: ResendOTPRequest):
    user = user_collection.find_one({"email": data.email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.get("is_verified"):
        return {"success": False, "error": "User already verified"}

    otp = str(random.randint(100000, 999999))
    otp_expiry = datetime.now(timezone.utc) + timedelta(minutes=10)
    user_collection.update_one({"_id": user["_id"]}, {"$set": {"otp": otp, "otp_expiry": otp_expiry}})
    await send_otp_email(data.email, otp)
    return {"success": True, "message": "OTP resent successfully"}

@app.post("/login")
def login(user: UserLogin):
    user_data = user_collection.find_one({"email": user.email})
    if not user_data or not bcrypt.checkpw(user.password.encode('utf-8'), user_data["password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user_data.get("is_verified", False):
        raise HTTPException(status_code=403, detail="Email not verified")
    token = create_token(user.email)
    return {"access_token": token, "token_type": "bearer"}

    
def get_current_user(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid auth header")
    token = authorization.split(" ")[1]
    payload = decode_token(token)
    user_email = payload.get("email")
    user = user_collection.find_one({"email": user_email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "name": user["name"],
        "email": user["email"],
        "created_at": user["created_at"]
    }

@app.get("/profile")
def get_profile(current_user: dict = Depends(get_current_user)):
    return {"profile": current_user}

@app.post("/change-password")
def change_password(data: PasswordChangeRequest, current_user: dict = Depends(get_current_user)):
    user = user_collection.find_one({"email": current_user["email"]})

    if not bcrypt.checkpw(data.current_password.encode('utf-8'), user["password"]):
        raise HTTPException(status_code=403, detail="Current password is incorrect")
    
    new_hashed_pw = bcrypt.hashpw(data.new_password.encode('utf-8'), bcrypt.gensalt())

    user_collection.update_one(
        {"email": current_user["email"]},
        {"$set": {"password": new_hashed_pw}}
    )

    return {"msg": "Password changed successfully"}

@app.get("/login/google")
def login_with_google():
    redirect_uri = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={GOOGLE_CLIENT_ID}"
        f"&redirect_uri={GOOGLE_REDIRECT_URI}"
        "&response_type=code"
        "&scope=openid%20email%20profile"
    )
    return RedirectResponse(url=redirect_uri)

@app.get("/auth/google/callback")
def google_callback(request: Request):
    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    token_url = "https://oauth2.googleapis.com/token"
    token_data = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code"
    }
    token_response = requests.post(token_url, data=token_data)
    if not token_response.ok:
        print("Token fetch error:", token_response.text)
        raise HTTPException(status_code=400, detail="Failed to fetch access token")
    
    token_json = token_response.json()
    access_token = token_json.get("access_token")

    if not access_token:
        raise HTTPException(status_code=400, detail="Failed to get access token")

    # Get user info
    userinfo_response = requests.get(
        "https://www.googleapis.com/oauth2/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"}
    )

    if not userinfo_response.ok:
        print("Userinfo fetch error:", userinfo_response.text)
        raise HTTPException(status_code=400, detail="Failed to fetch user info")
    
    userinfo = userinfo_response.json()

    email = userinfo.get("email")
    name = userinfo.get("name")
    picture = userinfo.get("picture")

    user = user_collection.find_one({"email": email})
    if not user:
        user_collection.insert_one({
            "name": name,
            "email": email,
            "password": None,
            "created_at": datetime.now(timezone.utc)
        })

    payload = {
        "sub": email,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=JWT_EXPIRY_SECONDS)
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    # Redirect or respond with session info
    return RedirectResponse(f"https://findmygadget.shop/chat.html?token={token}", status_code=302)

@app.post("/forget-password")
async def forget_password(data: ResendOTPRequest):
    user = user_collection.find_one({"email": data.email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    otp = str(random.randint(100000, 999999))
    otp_expiry = datetime.now(timezone.utc) + timedelta(minutes=10)

    user_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {"reset_otp": otp, "reset_otp_expiry": otp_expiry}}
    )

    await send_otp_email(data.email, otp)
    return {"success": True, "message": "OTP sent to your email. It will expire in 10 minutes"}

@app.post("/verify-reset-otp")
def verify_reset_otp(data: VerifyResetOTPRequest):
    user = user_collection.find_one({"email": data.email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user.get("reset_otp") != data.otp:
        raise JSONResponse(status_code=404, content={"success": False, "error": "Invalid OTP"})
    
    # if user.get("reset_otp_expiry") < datetime.now(timezone.utc):
    #     return JSONResponse(status_code=400, content={"success": False, "error": "OTP expired"})
    expiry = user.get("reset_otp_expiry")
    if expiry:
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        if expiry < datetime.now(timezone.utc):
            return JSONResponse(status_code=400, content={"success": False, "error": "OTP expired"})
    
    return {"success": True, "message": "OTP verified. You can now reset your password."}

@app.post("/reset-password")
def reset_password(data: ResetPassword):
    user = user_collection.find_one({"email": data.email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user.get("reset_otp") != data.otp:
        raise JSONResponse(status_code=400, content={"success": False, "error": "Invalid OTP"})
    
    # if user.get("reset_otp_expiry") < datetime.now(timezone.utc):
    #     return JSONResponse(status_code=400, content={"success": False, "error": "OTP expired"})
    expiry = user.get("reset_otp_expiry")
    if expiry:
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        if expiry < datetime.now(timezone.utc):
            return JSONResponse(status_code=400, content={"success": False, "error": "OTP expired"})
    
    hashed_pw = bcrypt.hashpw(data.new_password.encode('utf-8'), bcrypt.gensalt())

    user_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {"password": hashed_pw}, "$unset": {"reset_otp": "", "reset_otp_request": ""}}
    )

    return {"success": True, "message": "Password reset successfully. You can now login with new password."}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)