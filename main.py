import os
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Mount, Route
from starlette.types import Scope, Receive, Send  # NEW
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

# ----- MCP tools -----
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

# ----- Health -----
async def healthz(_):
    return PlainTextResponse("ok")

# ----- Auth middleware (tolerant of quotes; allows GET/HEAD probes) -----
class BearerAuth(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Let GET/HEAD/OPTIONS pass (Agent Builder probes without auth)
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)

        required = (os.environ.get("MCP_BEARER_TOKEN") or "").strip()
        auth = (request.headers.get("authorization") or "").strip()

        # Strip accidental surrounding quotes
        if len(auth) >= 2 and auth[0] == '"' and auth[-1] == '"':
            auth = auth[1:-1].strip()

        # Accept either "Bearer <token>" or "<token>"
        token = auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else auth

        if required and token != required:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        return await call_next(request)

# ----- MCP HTTP entry: 200 for GET/HEAD; delegate POST to real MCP app -----
mcp_http = mcp.streamable_http_app()

async def mcp_entry(scope: Scope, receive: Receive, send: Send):
    # Return 200 for GET/HEAD probes
    if scope["type"] == "http" and scope.get("method") in ("GET", "HEAD"):
        resp = JSONResponse({"status": "ok", "server": "GoogleAds-MCP"})
        await resp(scope, receive, send)
        return
    # Delegate POST/stream to the MCP ASGI app
    await mcp_http(scope, receive, send)

# ----- App / Routes -----
app = Starlette(
    routes=[
        Route("/healthz", healthz),
        # Single entry for both forms of the path; no 405s on POST
        Mount("/mcp", app=mcp_entry),
        Mount("/mcp/", app=mcp_entry),
    ],
)
app.add_middleware(BearerAuth)

# Prevent '/mcp' -> '/mcp/' auto-redirects
app.router.redirect_slashes = False

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
