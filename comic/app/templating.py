"""The Jinja templates shared by the API fragments and the dashboard pages."""

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")
