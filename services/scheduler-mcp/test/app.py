import os
import sys
import importlib.util
base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, base_dir)
spec = importlib.util.spec_from_file_location('scheduler_app', os.path.join(base_dir, 'app.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
app = module.app
