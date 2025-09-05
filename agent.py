from typing import TypedDict, List
from langgraph.graph import StateGraph, START, END
from langchain_core.prompts import PromptTemplate
import re
import requests 
import json
from dotenv import load_dotenv
import os
from google import genai
import asyncio
import aiohttp


load_dotenv()

client_gemini = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def gemi_invoke(prompt: str) -> str:
    try:
        response = client_gemini.models.generate_content(
            model = "gemini-2.0-flash",
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        print("Gemini API Error: ", e)
        return ""

class agentstate(TypedDict):
    query: str
    budget: int
    budget_buffer: int
    category: str
    product: str
    brand: str
    product_list: List[dict]
    recommendation: str
    followup_answer: str

prompt_is_gadget = PromptTemplate(
    template = '''
        Check if the user query is related to electronic gadgets like laptops, mobiles, tablets, smartwatches etc
        Respond only with "yes" or "no".

        Query: {query}
    ''',
    input_variables=['query']
)

prompt_extract = PromptTemplate(
    template='''
        You are a helpful AI assistant tasked with extracting details from a user's query.

        Extract the following clearly:
        - Budget (digits only, 0 if none mentioned),
        - Product category (e.g., laptop, mobile, smartwatch),
        - Intended use case (e.g., gaming, video editing, general use). If no use case is specified, use "GENERAL".
        - Brand name, if mentioned. If no brand mentioned return "not_mentioned".

        Output ONLY a valid JSON object in this exact format:

        {{
            "budget": "...",
            "category": "...",
            "usecase": "...",
            "brand": "..."
        }}

        User query: {query}
    ''',
    input_variables=["query"]
)

prompt_recommend = PromptTemplate(
    template='''
        You are an expert electronic gadget assistant providing professional and courteous recommendations.

        Given:
        - A list of electronic products (with price, rating, review count, positivity percentage and final weighted score),
        - The user's budget,
        - The product category (e.g., laptop, mobile),
        - The user's intended use case (e.g., gaming, video editing, general use),

        Your task:
        1. Analyze the products carefully based on:
           - **Final Weighted Score** (most important: reliability of reviews),
           - Suitability for the use case,
           - Percentage of positive reviews (secondary factor),
           - Number of reviews (prefer more reviews if scores are close),
           - Prioritize products **within the original budget**,
           - If none are strong, allow products slightly over budget (within Flexible budget).

        2. Select the **Top 3 products** from the list, and clearly rank them as:
           🥇 Best Overall - balanced choice for most users,
           🥈 Best Value - most budget-friendly option with decent quality,
           🥉 Premium Option - slightly over budget (if necessary) but worth it.

        3. For each recommendation, provide:
           - Key strengths of the product,
           - Why it stands out compared to others,
           - Final Weighted Score + positivity percentage + review count summary,
           - Mention clearly whether it is "within budget" OR "slightly over budget (but worth it)".
           - **DIRECT CLICKABLE PRODUCT LINK** (use the one from the product list).

        4. Do not fabricate or invent products. Recommend only from the given list.

        5. Remember:
           - Recommend only from the given list.
           - Do not fabricate or invent any products.
           - Maintain a polite and professional tone.
           - Keep your response concise and clear.

        Original Budget: {budget}
        Flexible Budget: {budget_buffer}
        Category: {category}
        Product Type: {product}
        Product List:
        {product_list}
    ''',
    input_variables=['budget', 'budget_buffer', 'category', 'product', 'product_list']
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

def check_is_greeting(state: agentstate) -> str:
    greetings = ["hi", "hello", "hey", "good morning", "good evening", "good afternoon"]
    q = state['query'].lower().strip()
    return "yes" if q in greetings else "no"

def handle_greeting(state: agentstate) -> agentstate:
    state['recommendation'] = "Hello! How can I assist you with gadgets today?"
    return state

def check_is_gadget_query(state: agentstate) -> str:
    result = gemi_invoke(prompt_is_gadget.format(query=state['query']))
    result = result.strip().lower()
    return result

def is_gadget_query(state: agentstate) -> agentstate:
    return state

def response_to_non_gadget(state: agentstate) -> agentstate:
    state['recommendation'] = "Sorry, I only respond to gadget-related questions."
    return state

async def fetch_reviews_async(session, asin):
    if not asin:
        return []
    url = "https://real-time-amazon-data.p.rapidapi.com/product-reviews"
    headers = {
        "x-rapidapi-key": os.getenv("RAPIDAPI_KEY"),
        "x-rapidapi-host": "real-time-amazon-data.p.rapidapi.com"
    }
    params = {
        "asin": asin,
        "country": "IN",
        "page": 1,
        "sort_by": "TOP_REVIEWS"
    }
    async with session.get(url, headers=headers, params=params, timeout=10) as resp:
        try:
            data = await resp.json(content_type=None)
        except Exception as e:
            text = await resp.text()
            print(f"❌ JSON parse error: {e} | Response text: {text[:200]}")
            return []
        
        if not isinstance(data, dict):
            print(f"❌ API returned non-JSON data: {data}")
            return []
        
        reviews = data.get("data", {}).get("reviews", [])
        return [r.get("review_text", "") for r in reviews if isinstance(r, dict)]
    
def extract_asin(url: str) -> str:
    match = re.search(r'/dp/([A-Z0-9]{10})', url)
    return match.group(1) if match else ""

def batch_sentiment_analysis(product_reviews):
    prompt = '''
        You are a sentiment classifier. For each product, return counts in JSON:
        {
        "<index>": {"positive": X, "negative": Y, "neutral": Z, "total": T}
        }
        Reviews grouped per product index:
    '''
    for idx, reviews in enumerate(product_reviews):
        prompt += f"\nProduct {idx}:\n"
        for r in reviews:
            prompt += f"- {r}"
    
    result_text = gemi_invoke(prompt)
    try:
        return json.loads(result_text)
    except:
        return {}

def compute_weighted_score(positive, total, m=100, C=70):
    if total == 0:
        return 0
    R = (positive / total) * 100
    v = total
    score = (v / (v + m)) * R + (m / (v + m)) * C
    return round(score, 2)
    
def add_affiliate_tag(url: str, tag: str) -> str:
    if not url:
        return url
    if "tag=" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}tag={tag}"

async def product_async(state: agentstate):
    if state['category'].lower() != "general":
        query_str = f"{state['product']} for {state['category']}"
    else:
        query_str = f"{state['product']}"
    budget = int(state.get('budget', 0))
    budget_buffer = int(budget * 1.2) if budget else 0
    state['budget_buffer'] = budget_buffer

    url = "https://real-time-amazon-data.p.rapidapi.com/search"
    headers = {
        "x-rapidapi-key": os.getenv("RAPIDAPI_KEY"),
        "x-rapidapi-host": "real-time-amazon-data.p.rapidapi.com"
    }
    params = {
        "query": query_str,
        "page": 1,
        "country": "IN",
        "sort_by": "RELEVANCE",
        "product_condition": "ALL",
    }
    if budget_buffer > 0:
        params['max_price'] = budget_buffer
        params["min_price"] = max(0, budget * 0.7)
        # state['budget'] = budget_buffer

    if state['brand'] != "not_mentioned":
        params['brand'] = state['brand']

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params, timeout=15) as resp:
            data = await resp.json()
            products = data.get("data", {}).get("products", [])
        
        tasks = []
        filtered_products = []
        for p in products:
            asin = extract_asin(p.get("product_url", ""))
            if not asin:
                continue
            filtered_products.append({
                "title": p.get("product_title"),
                "price": p.get("product_minimum_offer_price"),
                "original_price": p.get("product_original_price", "N/A"),
                "url": add_affiliate_tag(p.get("product_url"), os.getenv("AFFILIATE_TAG")),
                "rating": p.get("product_star_rating", "N/A"),
                "image": p.get("product_photo", "N/A"),
                "asin": asin
            })
            tasks.append(fetch_reviews_async(session, asin))

        all_reviews = await asyncio.gather(*tasks)
    
    sentiments = batch_sentiment_analysis(all_reviews)

    for idx, p in enumerate(filtered_products):
        sentiment = sentiments.get(str(idx), {"positive": 0, "negative": 0, "neutral": 0, "total": 0})
        p["review_sentiment"] = sentiment
        p["positive_percent"] = ((sentiment['positive'] / sentiment['total']) * 100) if sentiment['total'] > 0 else 0
        p["final_score"] = compute_weighted_score(
            sentiment["positive"], sentiment["total"],
            m = 100,
            C = 70
        )

    state['product_list'] = filtered_products
    return state

def route_query(state: agentstate) -> str:
    result = gemi_invoke(prompt_classifier.format(query=state["query"])).lower()
    return result

def classify_query_node(state: agentstate) -> agentstate:
    return state

def detect_followup_node(state: agentstate) -> agentstate:
    return state

def handle_informational(state: agentstate) -> agentstate:
    state["recommendation"] = gemi_invoke(f"""
        You are a polite and knowledgeable assistant.

        User's question:
        {state['query']}

        Please provide a clear, respectful, and concise answer.
    """)
    return state

def detect_followup(state: agentstate) -> str:
    result = gemi_invoke(prompt_followup.format(query=state['query'])).lower()
    result = result.strip().lower()
    if result not in ["new", "followup"]:
        result = "new"
    return result

def for_extracting(state: agentstate) -> agentstate:
    response_text = gemi_invoke(prompt_extract.format(query=state['query']))
    if response_text.startswith("```json"):
        lines = response_text.splitlines()
        json_lines = [line for line in lines if line.strip() not in ("```json", "```")]
        clean_response_text = "\n".join(json_lines)
    else:
        clean_response_text = response_text
    try:
        parsed = json.loads(clean_response_text)
    except json.JSONDecodeError:
        parsed = {"budget": 0, "category": "unknown", "usecase": "GENERAL"}
    state["budget"] = int(parsed.get("budget", 0))
    state["product"] = parsed.get("category", "unknown")
    state["category"] = parsed.get("usecase", "GENERAL")
    state["brand"] = parsed.get("brand", "not_mentioned")
    return state

def product(state: agentstate) -> agentstate:
    return asyncio.run(product_async(state))

def recommendation(state: agentstate) -> agentstate:
    if not state['product_list']:
        state["recommendation"] = (
            "⚠️ Sorry, I couldn't find any relevant products under your budget. "
            "Please try rephrasing your query or try again later."
        )
        return state
    
    sorted_products = sorted(
        state["product_list"], key=lambda x: x.get("final_score", 0), reverse=True
    )
    
    product_list_str = "\n".join([
        f"{p['title']} | {p['price']} | {p['original_price']} | {p['rating']}⭐ | {p['review_sentiment']['positive']}👍 | {p['review_sentiment']['negative']}👎 | {round(p.get('positivity_percent', 0), 2)}% positive | Final Score: {p['final_score']} | {p['url']}"
        for p in sorted_products
    ])

    prompt_text = prompt_recommend.format(
        budget=state['budget'],
        budget_buffer=state['budget_buffer'],
        category=state['category'],
        product=state['product'],
        product_list=product_list_str
    )

    response = gemi_invoke(prompt_text)
    output = response.strip()

    state['recommendation'] = output
    return state

def handle_followup(state: agentstate) -> agentstate:
    product_list_str = "\n".join([
        f"{p['title']} | {p['price']} | {p['rating']}"
        for p in state['product_list']
    ]) if isinstance(state['product_list'], list) else "No product Found"

    prompt = f'''
            You are a professional AI assistant continuing a product recommendation conversation.

            Here is the previous recommendation you gave:

            {state.get("recommendation", "No previous recommendation")}

            Product list:
            {product_list_str}

            The user has now asked this follow-up question:
            {state["query"]}

            Please respond:
            - Using only the products from the list above,
            - Without introducing any new products,
            - Clearly and politely addressing the user's question,
            - Being concise and helpful.

            Respond respectfully and professionally.
        '''
    response = gemi_invoke(prompt)
    state['followup_answer'] = response.strip()
    return state

graph = StateGraph(agentstate)

graph.add_node("is_gadget_query", is_gadget_query)
graph.add_node("response_to_non_gadget", response_to_non_gadget)
graph.add_node("classify_query_node", classify_query_node)
graph.add_node("handle_informational", handle_informational)
graph.add_node("detect_followup_node", detect_followup_node)
graph.add_node("for_extracting", for_extracting)
graph.add_node("product", product)
graph.add_node("recommendation", recommendation)
graph.add_node("handle_followup", handle_followup)
graph.add_node("handle_greeting", handle_greeting)

graph.add_conditional_edges(START, check_is_greeting, {
    "yes": "handle_greeting",
    "no": "is_gadget_query"
})
graph.add_conditional_edges("is_gadget_query", check_is_gadget_query,{
    "yes": "classify_query_node",
    "no": "response_to_non_gadget"
})
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
graph.add_edge("response_to_non_gadget", END)

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
