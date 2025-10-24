import os
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Mount, Route
from mcp.server.fastmcp import FastMCP
from google.ads.googleads.client import GoogleAdsClient
from google.protobuf.json_format import MessageToDict

# Build a google-ads.yaml from env vars at runtime
def get_google_ads_client():
    yaml_text = f"""
developer_token: {os.environ['GOOGLE_ADS_DEVELOPER_TOKEN']}
client_id: {os.environ['GOOGLE_ADS_CLIENT_ID']}
client_secret: {os.environ['GOOGLE_ADS_CLIENT_SECRET']}
refresh_token: {os.environ['GOOGLE_ADS_REFRESH_TOKEN']}
login_customer_id: {os.environ.get('GOOGLE_ADS_LOGIN_CUSTOMER_ID', '')}
use_proto_plus: True
"""
    path = "/tmp/google-ads.yaml"
    with open(path, "w") as f:
        f.write(yaml_text)
    return GoogleAdsClient.load_from_storage(path)

mcp = FastMCP("GoogleAds-MCP")

@mcp.tool()
def list_accessible_customers():
    client = get_google_ads_client()
    svc = client.get_service("CustomerService")
    resp = svc.list_accessible_customers()
    return [rn.split("/")[-1] for rn in resp.resource_names]

@mcp.tool()
def search(customer_id: str, query: str, page_size: int = 50):
    client = get_google_ads_client()
    svc = client.get_service("GoogleAdsService")
    req = client.get_type("SearchGoogleAdsRequest")
    req.customer_id = customer_id
    req.query = query
    req.page_size = page_size
    rows = []
    for row in svc.search(request=req):
        rows.append(MessageToDict(row._pb, preserving_proto_field_name=True))
        if len(rows) >= 500:
            break
    return rows

# Simple bearer auth so only your agent can call it
class BearerAuth(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        required = os.environ.get("MCP_BEARER_TOKEN")
        if required:
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {required}":
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)

async def healthz(_):
    return PlainTextResponse("ok")

# Expose Streamable HTTP MCP at /mcp
app = Starlette(routes=[Route("/healthz", healthz), Mount("/mcp", mcp.streamable_http_app())])
app.add_middleware(BearerAuth)
