# 🎯 Gadgetry Backend

An intelligent AI-powered electronics gadget recommendation system backend built with **FastAPI**, **LangGraph**, and **MongoDB**. The system analyzes user queries using Gemini AI, fetches real-time product data and reviews, performs sentiment analysis, and provides personalized product recommendations.

## 📋 Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [API Endpoints](#api-endpoints)
- [Authentication](#authentication)
- [Project Structure](#project-structure)
- [Core Components](#core-components)
- [Deployment](#deployment)

## ✨ Features

### AI-Powered Recommendations
- **Natural Language Processing**: Understands user queries and extracts key parameters (budget, category, use case, brand)
- **Smart Query Classification**: Distinguishes between product recommendations and informational queries
- **Real-time Amazon Reviews**: Fetches live product reviews and ratings using RapidAPI
- **Sentiment Analysis**: Analyzes review sentiment to determine product quality and reliability
- **Weighted Scoring**: Implements a Bayesian rating system to rank products based on review positivity and count

### User Management
- **JWT Authentication**: Secure token-based authentication with configurable expiry
- **Google OAuth Integration**: Seamless sign-up/login with Google accounts
- **Email Verification**: OTP-based email verification for account creation
- **Password Management**: Secure password reset with OTP verification using bcrypt hashing

### Session Management
- **User Sessions**: Track user interactions and maintain context for follow-up queries
- **Query History**: Store and retrieve previous queries and recommendations

### API Features
- **CORS Support**: Configured for production domain (findmygadget.shop)
- **Error Handling**: Comprehensive exception handling with detailed error responses
- **Async Support**: Built with FastAPI's async capabilities for high performance

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Server                            │
│  (main.py - HTTP REST API & Request Handler)                │
└──────────────────────┬──────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
┌───────▼─────┐  ┌────▼─────┐  ┌────▼──────────┐
│  LangGraph  │  │ MongoDB   │  │ Gemini AI    │
│  Workflow   │  │ Storage   │  │ NLP Engine   │
│ (agent.py)  │  │           │  │              │
└──────┬──────┘  └───────────┘  └────┬─────────┘
       │                              │
       ├─────────────────────────────┤
       │                              │
┌──────▼───────────────────────┐ ┌──▼─────────────────┐
│ Query Analysis & Extraction  │ │ Amazon RapidAPI   │
│ - Budget Detection           │ │ - Product Reviews │
│ - Category Classification    │ │ - Ratings & Stats │
│ - Use Case Identification    │ │ - Metadata        │
└──────────────────────────────┘ └───────────────────┘
```

### Agent Workflow (LangGraph)
1. **Query Classification**: Determines if query is gadget-related or greeting
2. **Parameter Extraction**: Extracts budget, category, use case, and brand preference
3. **Product Search**: Searches for matching products with Amazon data
4. **Review Fetching**: Asynchronously fetches reviews for all products
5. **Sentiment Analysis**: Batch analyzes review sentiment
6. **Scoring & Ranking**: Computes weighted scores for products
7. **Recommendation Generation**: Creates personalized top-3 recommendations

## 🛠️ Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Framework** | FastAPI | HTTP REST API framework |
| **Server** | Uvicorn | ASGI server |
| **Database** | MongoDB | User & session storage |
| **AI/ML** | Google Gemini 2.5 Flash | NLP & query understanding |
| **Workflow** | LangGraph | Multi-step AI orchestration |
| **Authentication** | JWT + OAuth2 | Secure user authentication |
| **Email** | FastAPI-Mail + Gmail SMTP | Email verification |
| **API Integration** | RapidAPI | Amazon product data |
| **Data Validation** | Pydantic | Request/response validation |
| **Password Security** | bcrypt | Secure password hashing |

## 📦 Prerequisites

- **Python 3.9+**
- **MongoDB** (Atlas cloud or local instance)
- **Google Cloud API Key** (Gemini API)
- **RapidAPI Key** (Amazon Real-Time Data)
- **Google OAuth Credentials** (Client ID & Secret)
- **Gmail Account** (for email verification)

## 🚀 Installation

### 1. Clone the Repository
```bash
git clone https://github.com/yourusername/gadgetry-backend.git
cd gadgetry-backend
```

### 2. Create Virtual Environment
```bash
python -m venv venv

# On Windows
venv\Scripts\activate

# On macOS/Linux
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

## ⚙️ Configuration

### Environment Variables (.env file)

Create a `.env` file in the root directory with the following variables:

```env
# MongoDB Configuration
MONGO_URI=mongodb+srv://username:password@cluster.mongodb.net/

# JWT Configuration
SECRET_KEY=your-secret-key-min-32-chars
JWT_ALGORITHM=HS256

# Google OAuth
GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-google-client-secret
GOOGLE_REDIRECT_URI=https://findmygadget.shop/auth/google/callback

# Gemini API
GEMINI_API_KEY=your-gemini-api-key

# RapidAPI (Amazon Data)
RAPIDAPI_KEY=your-rapidapi-key

# Email Configuration
EMAIL_USER=your-gmail@gmail.com
EMAIL_PASS=your-app-specific-password
```

**Important Notes:**
- `SECRET_KEY` should be at least 32 characters long
- Use Gmail App Passwords (not regular password) for `EMAIL_PASS`
- Ensure MongoDB URI has proper URL encoding for special characters
- CORS origins are hardcoded to production domains - modify in `main.py` for development

## 📡 API Endpoints

### Authentication

#### Register User
```http
POST /register
Content-Type: application/json

{
  "name": "John Doe",
  "email": "john@example.com",
  "password": "securepassword123"
}
```

#### Verify Email OTP
```http
POST /verify-otp
Content-Type: application/json

{
  "email": "john@example.com",
  "otp": "123456"
}
```

#### Login
```http
POST /login
Content-Type: application/json

{
  "email": "john@example.com",
  "password": "securepassword123"
}
```

#### Google OAuth Login
```http
GET /auth/google
```

#### Request Password Reset
```http
POST /forgot-password
Content-Type: application/json

{
  "email": "john@example.com"
}
```

#### Reset Password with OTP
```http
POST /reset-password
Content-Type: application/json

{
  "email": "john@example.com",
  "otp": "123456",
  "new_password": "newpassword123"
}
```

### Recommendations

#### Get Product Recommendation
```http
POST /query
Authorization: Bearer <jwt-token>
Content-Type: application/json

{
  "query": "I need a gaming laptop under $1000",
  "session_id": "session-123"
}
```

**Response:**
```json
{
  "recommendation": "🥇 Best Overall: [Product details & scores]\n🥈 Best Value: [...]\n🥉 Premium Option: [...]",
  "product_list": [
    {
      "title": "Product Name",
      "price": "$999",
      "rating": 4.5,
      "review_count": 250,
      "positive_percentage": 85,
      "weighted_score": 90,
      "link": "https://amazon.in/dp/B0123456789"
    }
  ]
}
```

#### Get User Profile
```http
GET /profile
Authorization: Bearer <jwt-token>
```

#### Change Password
```http
POST /change-password
Authorization: Bearer <jwt-token>
Content-Type: application/json

{
  "current_password": "oldpassword123",
  "new_password": "newpassword123"
}
```

## 🔐 Authentication

### JWT Token
- **Expiry**: 3600 seconds (1 hour)
- **Algorithm**: HS256
- **Header**: `Authorization: Bearer <token>`

### Session Management
- Each user can have multiple sessions
- Sessions stored in MongoDB with unique session IDs
- Query history tracked per session for context

## 📁 Project Structure

```
gadgetry-backend/
├── main.py                  # FastAPI app, routes, and request handlers
├── agent.py                 # LangGraph workflow, AI logic, and NLP functions
├── requirements.txt         # Python dependencies
├── Procfile                 # Heroku deployment configuration
├── .env                     # Environment variables (not in repo)
└── README.md               # This file
```

### main.py Overview
- **FastAPI Application**: REST API endpoints
- **Database Models**: User, Session collections
- **Authentication**: JWT token generation and validation
- **Request Handlers**: Google OAuth, email verification, password reset
- **Query Processing**: Endpoint to handle product recommendation requests

### agent.py Overview
- **LangGraph Workflow**: State machine for query processing
- **Gemini Integration**: LLM prompts for NLP tasks
- **Review Fetching**: Async RapidAPI calls for product reviews
- **Sentiment Analysis**: Batch sentiment classification
- **Scoring System**: Bayesian rating algorithm (weighted score calculation)

## 🧠 Core Components

### 1. Query Classification
- Detects if query is gadget-related
- Identifies greetings
- Classifies between recommendation and informational queries

### 2. Parameter Extraction
```python
{
  "budget": 999,                    # User's budget in numbers
  "category": "laptop",              # Product category
  "usecase": "gaming",               # Intended use case
  "brand": "Dell or HP"             # Brand preference (if any)
}
```

### 3. Weighted Scoring Algorithm
Implements Bayesian rating formula:
```
Weighted Score = (positive_count + m*C) / (total_reviews + m) * 100

Where:
- m = Confidence factor (100)
- C = Expected positive percentage (70%)
- positive_count = Number of positive reviews
- total_reviews = Total number of reviews
```

This ensures products with few reviews but high ratings aren't ranked too high.

### 4. Recommendation Format
Top 3 products ranked as:
- 🥇 **Best Overall**: Balanced choice for most users
- 🥈 **Best Value**: Most budget-friendly with good quality
- 🥉 **Premium Option**: Slight over budget but worth it

## 🚀 Deployment

### Heroku Deployment
```bash
# Install Heroku CLI
# Login to Heroku
heroku login

# Create Heroku app
heroku create app-name

# Set environment variables
heroku config:set MONGO_URI=...
heroku config:set SECRET_KEY=...
heroku config:set GEMINI_API_KEY=...
# ... set other variables

# Deploy
git push heroku main
```

The `Procfile` automatically configures Uvicorn to run on `0.0.0.0` with the PORT set by Heroku.

### Local Development
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## 🧪 Testing Recommendations

### Example Queries
- "I need a gaming laptop under $1200"
- "Best budget smartphone for photography"
- "Which smartwatch is good for fitness tracking?"
- "Recommend a tablet for video editing under $500"

## 📝 Notes

- **CORS Restrictions**: Currently restricted to production domain. Update `allow_origins` in `main.py` for local development
- **Email Configuration**: Requires Gmail App Password, not regular password
- **RapidAPI Rate Limits**: Consider caching for high-traffic scenarios
- **Async Operations**: Review fetching is asynchronous for performance

## 🐛 Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| MongoDB Connection Error | Verify MONGO_URI and network access in Atlas |
| Gemini API Errors | Check API key validity and quota |
| Email not sending | Use Gmail App Password, enable "Less secure app access" |
| CORS Errors | Add your domain to `allow_origins` in main.py |
| Token Expired | Increase JWT_EXPIRY_SECONDS or refresh token |

## 📄 License

[Add your license here]

## 👨‍💼 Author

Tanuj Rajput

## 🤝 Contributing

[Add contribution guidelines here]

---

**Live Application**: https://findmygadget.shop
