import sys
import os

os.environ.setdefault("DB_PATH", "/tmp/saas.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from main import app
