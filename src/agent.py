import json
#from json import tool
from anthropic import Anthropic, beta_tool
from dotenv import load_dotenv

load_dotenv()

client = Anthropic()

@beta_tool
def create_order(auth_token: str, ) :

    
    
