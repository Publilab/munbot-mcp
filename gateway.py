import os
import importlib.util
base = os.path.join(os.path.dirname(__file__), 'services', 'llm_docs-mcp', 'gateway.py')
spec = importlib.util.spec_from_file_location('llm_gateway', base)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
app = module.app
