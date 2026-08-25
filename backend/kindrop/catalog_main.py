from .config import RuntimeSettings
from .database import Database
from .opds import create_catalog_app

runtime = RuntimeSettings()
app = create_catalog_app(Database(runtime.database_url), runtime)
