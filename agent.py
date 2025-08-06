from typing import TypedDict, Literal, List, Annotated, NotRequired
from pydantic import Field
from langgraph.graph import StateGraph, START, END
from langchain_huggingface import HuggingFaceEndpoint, ChatHuggingFace
from langchain_core.prompts import PromptTemplate
import re
import requests
import json
from dotenv import load_dotenv
from huggingface_hub import InferenceClient
import os
import uuid
from pymongo import MongoClient

load_dotenv()

# sentiment_pipeline = pipeline("sentiment-analysis", model="distilbert/distilbert-base-uncased-finetuned-sst-2-english")
inference_client = InferenceClient(model="cardiffnlp/twitter-roberta-base-sentiment")

llm = HuggingFaceEndpoint(
    repo_id="Qwen/Qwen3-Coder-480B-A35B-Instruct",
    task="text-generation",
)
model = ChatHuggingFace(llm=llm)

client = MongoClient("mongodb://localhost:27017/")
db = client["gadgetry"]
session_collection = db["session_data"]

class agentstate(TypedDict):
    query: str
    budget: int
    category: str
    product: str
    product_list: List[dict]
    recommendation: str

prompt_extract = PromptTemplate(
    template='''
        You are helpful AI which will process the query given by the user and extract:
        - budget (in digits only), 0 if got mentioned.
        - product category (like laptop, mobile, etc.)
        - use case (like gaming, editing). If no use case is mentioned, take it as "GENERAL".

        Give the output strictly in this format:
        {{
            "budget": "...",
            "category": "...",
            "usecase": "..."
        }}

        Query: {query}
    ''',
    input_variables=["query"]
)

prompt_recommend = PromptTemplate(
    template='''
        You are a smart electronic gadget assistant.

        You are given:
        - A list of electronic products (with price, rating and review sentiment)
        - A user's budget
        - The product category(e.g., laptop, mobile)
        - The user's intended use case (e.g., gaming, video editing, general use)

        Your job is to:
        1. **Analyze all products** in the list and compare them based on:
            - Use-case scalability
            - Review sentiment (more positive, fewer negative)
            - Value for money (within budget, better specs for price)
            - User ratings
        2. **Select ONE best products** strictly from the list that best fits the user's needs.

        3. Justify your choice by:
            - Highlighting strengths of the chosen product
            - Mentioning why it stands out over others
            - Referencing review sentiment (e.g., "85% positive reviews") if available

        4. Finish with a friendly and confident final recommendation:
            - Mention the product name clearly
            - Include a short summary of why it's the best pick

        STRICT INSTRUCTIONS:
        - Recommend only from the list.
        - DO NOT make up products.
        - Be concise, objective and clear.      

        Budget: {budget}
        Category: {category}
        Product Type: {product}
        Product List:
        {product_list}
    ''',
    input_variables=['budget', 'category', 'product', 'product_list']
)

prompt_classifier = PromptTemplate(
    template='''
        Classify the user query as one of the following:
        - "recommendation" -> if the user is asking for a product suggestion.
        - "informational" -> if the user is asking a general question or doubt.

        Query: {query}

        Respond ONLY with one word: recommendation or informational
    ''',
    input_variables=["query"]
)

prompt_followup = PromptTemplate(
    template='''
        Is the following query a:
        - "new" request
        - "followup" to a previous product recommendation?

        Query: {query}

        Respond ONLY with new or followup
    ''',
    input_variables=["query"]
)

def extract_asin(url: str) -> str:
    match = re.search(r'/dp/([A-Z0-9]{10})', url)
    return match.group(1) if match else ""

def fetch_reviews(asin: str) -> List[str]:
    url = "https://real-time-amazon-data.p.rapidapi.com/product-reviews"
    headers = {
        "x-rapidapi-key": os.getenv("RAPIDAPI_KEY"),
        "x-rapidapi-host": "real-time-amazon-data.p.rapidapi.com"
    }
    params = {
        "asin": asin,
        "country": "IN",
        "page": 1,
        "sort_by": "TOP_REVIEWS",
        "star_rating": "ALL",
        "verified_purchases_only": "false",
        "images_or_videos_only": "false",
        "current_format_only": "false"
    }

    try:
        response = requests.get(url, headers=headers, params=params)
        data = response.json()
        reviews = [r.get("review_text", "") for r in data.get("data", {}).get("reviews", [])]
        return reviews
    except Exception as e:
        print("Review fetch error: ", e)
        return []
    
def analyze_sentiment_bulk(reviews: List[str]) -> dict:
    if not reviews:
        return {"positive": 0, "negative": 0, "neutral": 0, "total": 0}

    sentiment_counts = {"positive": 0, "negative": 0, "neutral": 0}
    try:
        for review in reviews:
            if not review:
                continue
            output = inference_client.text_classification(review)
            label = output[0].label
            if label == "LABEL_2":
                sentiment_counts["positive"]+=1
            elif label == "LABEL_1":
                sentiment_counts["neutral"]+=1
            elif label == "LABEL_0":
                sentiment_counts["negative"]+=1
    except Exception as e:
        print("Remote sentiment analysis error: ", e)
    sentiment_counts["total"] = sum(sentiment_counts.values())
    return sentiment_counts

def route_query(state: agentstate) -> str:
    query = state["query"]
    response = model.invoke(prompt_classifier.format(query=query))
    return response.content.strip().lower()

def classify_query_node(state: agentstate) -> agentstate:
    return state

def detect_followup_node(state: agentstate) -> agentstate:
    return state

def handle_informational(state: agentstate) -> agentstate:
    response = model.invoke(f"User asked: {state['query']}\nAnswer in a helpful and technical but simple way.")
    state['recommendation'] = response.content.strip()
    return state

def detect_followup(state: agentstate) -> str:
    response = model.invoke(prompt_followup.format(query=state['query']))
    return response.content.strip().lower()

def for_extracting(state: agentstate) -> agentstate:
    prompt_for_extracting = prompt_extract.format(query=state['query'])
    response = model.invoke(prompt_for_extracting)
    try:
        parsed = json.loads(response.content)
    except json.JSONDecodeError:
        print("❌ JSON Parsing failed. Response:", response.content)
        parsed = {"budget": 0, "category": "unknown", "usecase": "GENERAL"}
    state["budget"] = int(parsed.get("budget", 0))
    state["product"] = parsed.get("category", "unknown")
    state["category"] = parsed.get("usecase", "GENERAL")
    return state

def product(state: agentstate) -> agentstate:
    url = "https://real-time-amazon-data.p.rapidapi.com/search"
    headers = {
        "x-rapidapi-key": os.getenv("RAPIDAPI_KEY"),
        "x-rapidapi-host": "real-time-amazon-data.p.rapidapi.com"
    }

    params = {
        "query": f"{state['category']} {state['product']}",
        "page": "1",
        "country": "IN", 
        "sort_by": "RELEVANCE",
        "max_price": state['budget'], 
        "product_condition": "ALL", 
        "is_prime": "false",
        "deals_and_discounts": "NONE"
    }
    
    filtered_products = []
    
    try:
        response = requests.get(url, headers=headers, params=params)
        data = response.json()
        results = data.get("data", {}).get("products", [])

        print(f"\n🔍 DEBUG: Found {len(results)} raw products")

        for p in results:
            product_url = p.get("product_url")
            asin = extract_asin(product_url)
            if not asin:
                continue

            reviews = fetch_reviews(asin)
            sentiment = analyze_sentiment_bulk(reviews)

            product_info = {
                "title": p.get("product_title"),
                "price": p.get("product_minimum_offer_price"),
                "original_price": p.get("product_original_price", "N/A"),
                "url": p.get("product_url"),
                "rating": p.get("product_star_rating", "N/A"),
                "image": p.get("product_photo", "N/A"),
                "asin": asin,
                "review_sentiment": sentiment
            }

            print("🆕 Product:", product_info["title"])
            filtered_products.append(product_info)

    except Exception as e:
        print("API ERROR", e)
    
    state["product_list"] = filtered_products
    return state

def recommendation(state: agentstate) -> agentstate:
    if not state['product_list']:
        state["recommendation"] = (
            "⚠️ Sorry, I couldn't find any relevant products under your budget. "
            "Please try rephrasing your query or try again later."
        )
        return state
    
    product_list_str = "\n".join([
        f"{p['title']} | {p['price']} | {p['original_price']} | {p['rating']} | {p['review_sentiment']['positive']}👍 | {p['review_sentiment']['negative']}👎 | {p['url']}"
        for p in state['product_list']
    ])

    prompt_text = prompt_recommend.format(
        budget=state['budget'],
        category=state['category'],
        product=state['product'],
        product_list=product_list_str
    )

    response = model.invoke(prompt_text)
    output = response.content.strip()

    state['recommendation'] = output
    return state

def handle_followup(state: agentstate) -> agentstate:
    product_list_str = "\n".join([
        f"{p['title']} | {p['price']} | {p['rating']}"
        for p in state['product_list']
    ]) if isinstance(state['product_list'], list) else "No product Found"

    prompt = f'''
            You are a helpful AI assistant continuing a product recommendation conversation.
            Below is your previous recommendation and the list of the products you analyzed:

            Previous Recommendation:
            {state["recommendation"]}

            Product list:
            {product_list_str}

            The user is now asking a follow-up question:
            {state["query"]}

            Only use the products from the list above in your answer.
            - Do not introduce new products.
            - You may compare or explain choices from the list.
            - Be clear, concise and helpful.

            Respond appropriately:
        '''
    response = model.invoke(prompt)
    state['recommendation'] = response.content.strip()
    return state

graph = StateGraph(agentstate)

graph.add_node("classify_query_node", classify_query_node)
graph.add_node("handle_informational", handle_informational)
graph.add_node("detect_followup_node", detect_followup_node)
graph.add_node("for_extracting", for_extracting)
graph.add_node("product", product)
graph.add_node("recommendation", recommendation)
graph.add_node("handle_followup", handle_followup)

graph.add_edge(START, "classify_query_node")
graph.add_conditional_edges("classify_query_node", route_query, {
    "informational": "handle_informational",
    "recommendation": "detect_followup_node"
})
graph.add_conditional_edges("detect_followup_node", detect_followup, {
    "new": "for_extracting",
    "followup": "handle_followup"
})
graph.add_edge("for_extracting", "product")
graph.add_edge("product", "recommendation")
graph.add_edge("recommendation", END)
graph.add_edge("handle_followup", END)
graph.add_edge("handle_informational", END)

workflow = graph.compile()

# state: agentstate = {
#     "query": "",
#     "budget": 0,
#     "category": "",
#     "product": "",
#     "product_list": [],
#     "recommendation": ""
# }

# print("Welcome to Smart Gadget Assistant! Ask anything (type 'exit' to quit)")

# while True:
#     user_input = input("\n🧑 You: ")
#     if user_input.lower() in ["exit", "quit"]:
#         print("Goodbye!")
#         break

#     state['query'] = user_input
#     result = workflow.invoke(state)
#     print("\n🤖 Assistant:", result["recommendation"])


