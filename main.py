from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Depends, Header
from fastapi.responses import JSONResponse, RedirectResponse
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
    allow_origins=["https://findmygadget.shop"],
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

def decode_token(token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
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
    
def generate_verification_token():
    return secrets.token_urlsafe(32)

async def send_verification_email(email: str, token: str):
    verify_link = f"https://www.findmygadget.shop/verify/{token}"
    message = MessageSchema(
        subject="Verify your FindMyGadget Account",
        recipients=[email],
        body=f"""
            <p>Hi!</p>
            <p>Click the link below to verify your email and activate your account:</p>
            <a href="{verify_link}">Verify Email</a>
            <p>This link will expire in 1 hour.</p>
        """,
        subtype="html"
    )
    fm = FastMail(conf)
    await fm.send_message(message)

@app.post("/signup")
def signup(user: UserCreate):
    if user_collection.find_one({"email": user.email}):
        raise HTTPException(status_code=400, detail="Email already exist")
    
    hashed_pw = bcrypt.hashpw(user.password.encode('utf-8'), bcrypt.gensalt())
    token = generate_verification_token()

    user_data = {
        "name": user.name,
        "email": user.email,
        "password": hashed_pw,
        "created_at": datetime.now(timezone.utc),
        "token_expiry": datetime.now(timezone.utc) + timedelta(hours=1),
        "verification_token": token,
        "is_verified": False
    }
    user_collection.insert_one(user_data)

    # verify_link = f"https://www.findmygadget.shop/verify/{token}"
    # print(f"Verification link (send via email): {verify_link}")
    asyncio.create_task(send_verification_email(user.email, token))

    # token_data = {
    #     "sub": user.email,
    #     "exp": datetime.now(timezone.utc) + timedelta(hours=12)   
    # }
    # access_token = jwt.encode(token_data, JWT_SECRET, algorithm=JWT_ALGORITHM)
    # return {
    #     "msg": "Signup Successful",
    #     "token_type": "bearer",
    #     "access_token": access_token
    # }
    return {"msg": "Signup successfull, please verify your email before logging in."}

@app.post("/verify/{token}")
def verify_email(token: str):
    user = user_collection.find_one({"verification_token": token})
    if not user:
        raise HTTPException(status_code=400, detail="Invalid verification token")
    
    if user["token_expiry"] < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Verification link expired")

    user_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {"is_verified": True}, "$unset": {"verification_token": "", "token_expiry": ""}}
    )
    return {"msg": "Email verified successfully. You can now log in!"}

@app.post("/login")
def login(user: UserLogin):
    user_data = user_collection.find_one({"email": user.email})

    if not user_data or bcrypt.checkpw(user.password.encode('utf-8'), user_data["password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    if not user_data.get("is_verified", False):
        raise HTTPException(status_code=403, detail="Email not verified. Please check your inbox.")
    
    payload = {
        "sub": str(user_data["_id"]),
        "email": user_data["email"],
        "exp": datetime.now(timezone.utc) + timedelta(seconds=JWT_EXPIRY_SECONDS)
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "name": user_data["name"],
            "email": user_data["email"]
        }
    }

    
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


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)


# jodj yntg aqgk zscs