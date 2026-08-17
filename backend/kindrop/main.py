from .api import create_app
from .config import RuntimeSettings
from .database import Database

runtime = RuntimeSettings()
app = create_app(Database(runtime.database_url), runtime)
