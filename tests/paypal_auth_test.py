from auth.paypal_auth import get_access_token

token = get_access_token()

print("oauth authentication successful")
print("Token received:", bool(token))
