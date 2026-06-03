__version__ = "1.1.3"

import html
import os
from urllib.parse import urlparse

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from sanic import Sanic, response
from sanic.exceptions import NotFound
from jinja2 import Environment, FileSystemLoader

from core.models import LogEntry

load_dotenv()


def normalize_path(value, default=""):
    value = (value or default).strip()
    if value in ("", "/", "NONE"):
        return ""
    if "://" in value or value.startswith("//"):
        value = urlparse(value, scheme="http").path
    return "/" + value.strip("/")


def join_url_paths(*parts):
    path = "/".join(str(part).strip("/") for part in parts if str(part).strip("/"))
    return "/" + path if path else "/"


if "URL_PREFIX" in os.environ:
    print("Using the legacy config var `URL_PREFIX`, rename it to `LOG_URL_PREFIX`")
    prefix = os.environ["URL_PREFIX"]
else:
    prefix = os.getenv("LOG_URL_PREFIX", "/logs")

prefix = normalize_path(prefix)
base_url = normalize_path(os.getenv("BASE_URL") or os.getenv("BASE_PATH"))

MONGO_URI = os.getenv("MONGO_URI") or os.getenv("CONNECTION_URI")
if not MONGO_URI:
    print("No CONNECTION_URI config var found. "
          "Please enter your MongoDB connection URI in the configuration or .env file.")
    exit(1)

app = Sanic(__name__)
app.static(join_url_paths(base_url, "static"), "./static")

jinja_env = Environment(loader=FileSystemLoader("templates"))
jinja_env.globals["base_url"] = base_url


def render_template(name, *args, **kwargs):
    template = jinja_env.get_template(name + ".html")
    return response.html(template.render(*args, **kwargs))


app.ctx.render_template = render_template


def strtobool(val):
    """
    Copied from distutils.strtobool.

    Convert a string representation of truth to true (1) or false (0).

    True values are 'y', 'yes', 't', 'true', 'on', and '1'; false values
    are 'n', 'no', 'f', 'false', 'off', and '0'.  Raises ValueError if
    'val' is anything else.
    """
    val = val.lower()
    if val in ('y', 'yes', 't', 'true', 'on', '1'):
        return 1
    elif val in ('n', 'no', 'f', 'false', 'off', '0'):
        return 0
    else:
        raise ValueError("invalid truth value %r" % (val,))


@app.listener("before_server_start")
async def init(app, loop):
    app.ctx.db = AsyncIOMotorClient(MONGO_URI).modmail_bot
    use_attachment_proxy = strtobool(os.getenv("USE_ATTACHMENT_PROXY", "no"))
    if use_attachment_proxy:
        app.ctx.attachment_proxy_url = os.getenv("ATTACHMENT_PROXY_URL", "https://cdn.discordapp.xyz")
        app.ctx.attachment_proxy_url = html.escape(app.ctx.attachment_proxy_url).rstrip("/")
    else:
        app.ctx.attachment_proxy_url = None

@app.exception(NotFound)
async def not_found(request, exc):
    return render_template("not_found")


async def index(request):
    return render_template("index")


async def get_raw_logs_file(request, key):
    """Returns the plain text rendered log entry"""
    document = await app.ctx.db.logs.find_one({"key": key})

    if document is None:
        raise NotFound

    log_entry = LogEntry(app, document)

    return log_entry.render_plain_text()


async def get_logs_file(request, key):
    """Returns the html rendered log entry"""
    document = await app.ctx.db.logs.find_one({"key": key})

    if document is None:
        raise NotFound

    log_entry = LogEntry(app, document)

    return log_entry.render_html()


app.add_route(index, join_url_paths(base_url), methods=["GET"])
if base_url:
    app.add_route(index, base_url + "/", methods=["GET"])
app.add_route(get_raw_logs_file, join_url_paths(base_url, prefix, "raw/<key>"), methods=["GET"])
app.add_route(get_logs_file, join_url_paths(base_url, prefix, "<key>"), methods=["GET"])


if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", 8000)),
        debug=bool(os.getenv("DEBUG", False)),
    )
