import os 
import requests
from dotenv import load_dotenv

load_dotenv()

def get_access_token():

    client_id = os.getenv("PAYPAL_CLIENT_ID")
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET")
    base_url = os.getenv("PAYPAL_BASE_URL")

    if not all([client_id, client_secret, base_url]):
        raise ValueError("Missing paypal credentials")
    
    response = requests.post(
    f"{base_url}/v1/oauth2/token",
    auth=(client_id, client_secret),
    data = {"grant_type": "client_credentials"},
    headers = {"accept": "application/json"},
    timeout=30
    )

    response.raise_for_status()

    return response.json()["access_token"]