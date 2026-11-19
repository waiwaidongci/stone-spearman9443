import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault('DATABASE_URL', 'sqlite://')
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/15')
os.environ.setdefault('JWT_SECRET', 'test-secret')
