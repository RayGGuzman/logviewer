from datetime import datetime, timezone
import dateutil.parser
import re
from urllib.parse import parse_qs, urlparse

from sanic import response
from natural.date import duration

from .formatter import format_content_html


URL_RE = re.compile(
    r"\b(?:(?:https?|ftp|file)://|www\.|ftp\.)"
    r"(?:\([-a-zA-Z0-9+&@#/%?=~_|!:,\.\[\];]*\)|[-a-zA-Z0-9+&@#/%?=~_|!:,\.\[\];])*"
    r"(?:\([-a-zA-Z0-9+&@#/%?=~_|!:,\.\[\];]*\)|[-a-zA-Z0-9+&@#/%=~_|$])"
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".avif", ".svg"}
GIF_EXTENSIONS = {".gif"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v", ".ogg"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".oga"}
ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}
RICH_MEDIA_DOMAINS = {
    "tenor.com",
    "www.tenor.com",
    "media.tenor.com",
    "giphy.com",
    "www.giphy.com",
    "media.giphy.com",
}


def normalize_url(url):
    url = url.rstrip(".,!?)];")
    if url.startswith(("www.", "ftp.")):
        return "https://" + url
    return url


def get_extension(value):
    path = urlparse(value).path if "://" in value else value
    filename = path.rsplit("/", 1)[-1]
    if "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[-1].lower()


def filename_from_url(url):
    path = urlparse(url).path
    filename = path.rsplit("/", 1)[-1]
    return filename or urlparse(url).netloc or "link"


def format_file_size(size):
    if not size:
        return ""

    units = ("B", "KB", "MB", "GB")
    amount = float(size)

    for unit in units:
        if amount < 1024 or unit == units[-1]:
            break
        amount /= 1024

    if unit == "B":
        return f"{int(amount)} {unit}"
    return f"{amount:.2f} {unit}"


def youtube_embed_url(url):
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.")
    video_id = None

    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "youtube-nocookie.com"):
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [None])[0]
        elif parsed.path.startswith(("/shorts/", "/embed/")):
            video_id = parsed.path.strip("/").split("/")[1]

    if not video_id or not re.match(r"^[A-Za-z0-9_-]{6,}$", video_id):
        return None

    return f"https://www.youtube.com/embed/{video_id}"


def preview_kind(url):
    if youtube_embed_url(url):
        return "youtube"

    extension = get_extension(url)
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in GIF_EXTENSIONS:
        return "gif"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension in AUDIO_EXTENSIONS:
        return "audio"
    if extension in ARCHIVE_EXTENSIONS:
        return "archive"
    return "file"


class LogEntry:
    def __init__(self, app, data):
        self.app = app
        self.key = data["key"]
        self.open = data["open"]
        self.created_at = dateutil.parser.parse(data["created_at"]).astimezone(timezone.utc)
        self.human_created_at = duration(self.created_at, now=datetime.now(timezone.utc))
        self.closed_at = (
            dateutil.parser.parse(data["closed_at"]).astimezone(timezone.utc) if not self.open else None
        )
        self.channel_id = int(data["channel_id"])
        self.guild_id = int(data["guild_id"])
        self.creator = User(app, data["creator"])
        self.recipient = User(app, data["recipient"])
        self.closer = User(app, data["closer"]) if not self.open else None
        self.close_message = format_content_html(data.get("close_message") or "")
        self.messages = [Message(app, m) for m in data["messages"]]
        self.internal_messages = [m for m in self.messages if m.type == "internal"]
        self.thread_messages = [
            m for m in self.messages if m.type not in ("internal", "system")
        ]

    @property
    def system_avatar_url(self):
        return "/static/img/system-icon.png"

    @property
    def human_closed_at(self):
        return duration(self.closed_at, now=datetime.now(timezone.utc))

    @property
    def message_groups(self):
        groups = []

        if not self.messages:
            return groups

        curr = MessageGroup(self.messages[0].author)

        for index, message in enumerate(self.messages):
            next_index = index + 1 if index + 1 < len(self.messages) else index
            next_message = self.messages[next_index]

            curr.messages.append(message)

            if message.is_different_from(next_message):
                groups.append(curr)
                curr = MessageGroup(next_message.author)

        groups.append(curr)
        return groups

    def render_html(self):
        return self.app.ctx.render_template("logbase", log_entry=self)

    def render_plain_text(self):
        messages = self.messages
        thread_create_time = self.created_at.strftime("%d %b %Y - %H:%M UTC")
        out = f"Thread created at {thread_create_time}\n"

        if self.creator == self.recipient:
            out += f"[R] {self.creator} "
            out += f"({self.creator.id}) created a Modmail thread. \n"
        else:
            out += f"[M] {self.creator} "
            out += f"created a thread with [R] "
            out += f"{self.recipient} ({self.recipient.id})\n"

        out += "────────────────────────────────────────────────\n"

        if messages:
            for index, message in enumerate(messages):
                next_index = index + 1 if index + 1 < len(messages) else index
                curr, next_ = message.author, messages[next_index].author

                author = curr
                user_type = "M" if author.mod else "R"
                create_time = message.created_at.strftime("%d/%m %H:%M")

                base = f"{create_time} {user_type} "
                base += f"{author}: {message.raw_content}\n"

                for attachment in message.attachments:
                    base += f"Attachment: {attachment}\n"

                out += base

                if curr != next_:
                    out += "────────────────────────────────\n"
                    current_author = author

        if not self.open:
            if messages:  # only add if at least 1 message was sent
                out += "────────────────────────────────────────────────\n"

            out += f"[M] {self.closer} ({self.closer.id}) "
            out += "closed the Modmail thread. \n"

            closed_time = self.closed_at.strftime("%d %b %Y - %H:%M UTC")
            out += f"Thread closed at {closed_time} \n"

        return response.text(out)


class User:
    def __init__(self, app, data):
        self.app = app
        self.id = int(data.get("id"))
        self.name = data["name"]
        self.discriminator = data["discriminator"]
        self.avatar_url = data["avatar_url"]
        self.mod = data["mod"]

    @property
    def default_avatar_url(self):
        return "https://cdn.discordapp.com/embed/avatars/{}.png".format(
            int(self.discriminator) % 5
        )

    def __str__(self):
        return f"{self.name}#{self.discriminator}"

    def __eq__(self, other):
        return self.id == other.id and self.mod is other.mod


class MessageGroup:
    def __init__(self, author):
        self.author = author
        self.messages = []

    @property
    def created_at(self):
        return self.messages[0].human_created_at

    @property
    def type(self):
        return self.messages[0].type


class Attachment:
    def __init__(self, app, data):
        self.app = app
        if isinstance(data, str):  # Backwards compatibility
            self.id = 0
            self.filename = "attachment"
            self.url = data
            self.is_image = True
            self.size = 0
        else:
            self.id = int(data["id"])
            self.filename = data["filename"]
            self.url = data["url"]
            self.is_image = data["is_image"]
            self.size = data["size"]
        if self.app.ctx.attachment_proxy_url is not None:
            self.url = self.url.replace("https://cdn.discordapp.com", self.app.ctx.attachment_proxy_url)
            self.url = self.url.replace("https://media.discordapp.net", self.app.ctx.attachment_proxy_url)
            print(self.url)

    @property
    def kind(self):
        if self.is_image:
            return "gif" if get_extension(self.filename) in GIF_EXTENSIONS else "image"
        return preview_kind(self.url if get_extension(self.url) else self.filename)

    @property
    def size_label(self):
        return format_file_size(self.size)

    @property
    def domain(self):
        return urlparse(self.url).netloc

    @property
    def icon_label(self):
        extension = get_extension(self.filename)
        if extension:
            return extension[1:4].upper()
        return "FILE"


class LinkPreview:
    def __init__(self, url):
        self.url = normalize_url(url)
        self.kind = preview_kind(self.url)
        self.filename = filename_from_url(self.url)
        self.domain = urlparse(self.url).netloc
        self.youtube_embed_url = youtube_embed_url(self.url)

    @property
    def title(self):
        return self.domain or self.filename

    @property
    def icon_label(self):
        extension = get_extension(self.filename)
        if extension:
            return extension[1:4].upper()
        return "LINK"


class Message:
    def __init__(self, app, data):
        self.app = app
        self.id = int(data["message_id"])
        self.created_at = dateutil.parser.parse(data["timestamp"]).astimezone(timezone.utc)
        self.human_created_at = duration(self.created_at, now=datetime.now(timezone.utc))
        self.raw_content = data["content"]
        self.attachments = [Attachment(app, a) for a in data["attachments"]]
        self.link_previews, skip_urls = self.extract_link_previews(
            self.raw_content,
            self.attachments,
        )
        self.content = self.format_html_content(
            self.raw_content,
            skip_urls=skip_urls,
        )
        self.author = User(app, data["author"])
        self.type = data.get("type", "thread_message")
        self.edited = data.get("edited", False)

    def is_different_from(self, other):
        return (
            (other.created_at - self.created_at).total_seconds() > 60
            or other.author != self.author
            or other.type != self.type
        )

    @staticmethod
    def format_html_content(content, skip_urls=None):
        return format_content_html(content, skip_urls=skip_urls)

    @staticmethod
    def extract_link_previews(content, attachments=None):
        previews = []
        skip_urls = set()
        seen_urls = set()
        attachments = attachments or []
        attachment_urls = {normalize_url(attachment.url) for attachment in attachments}
        has_media_attachment = any(
            attachment.kind in ("image", "gif", "video") for attachment in attachments
        )

        for match in URL_RE.finditer(content):
            url = normalize_url(match.group(0))
            if url in seen_urls:
                skip_urls.add(url)
                continue
            if url in attachment_urls:
                skip_urls.add(url)
                continue

            preview = LinkPreview(url)
            if (
                preview.kind == "file"
                and has_media_attachment
                and preview.domain.lower() in RICH_MEDIA_DOMAINS
            ):
                skip_urls.add(url)
                continue

            seen_urls.add(url)
            skip_urls.add(url)
            previews.append(preview)

        return previews, skip_urls
