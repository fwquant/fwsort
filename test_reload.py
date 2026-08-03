import sys
sys.path.insert(0, 'D:/git/github/fwquant/fwsort')
import importlib
import pkgutil

# Check current providers
from fwsort.signals.manager import _discover_providers, _PROVIDERS, _PROVIDER_INSTANCES, _PROVIDER_CATEGORIES
from fwsort.signals.base import SignalProvider
from loguru import logger

print('Initial providers:', list(_PROVIDERS.keys()))

# Now do what reload_providers does:
# 1. Reload base
base_module_name = 'fwsort.signals.base'
if base_module_name in sys.modules:
    try:
        importlib.reload(sys.modules[base_module_name])
        print('Reloaded base successfully')
    except Exception as e:
        print(f'Failed to reload base: {e}')

# 2. Reload providers
import fwsort.signals.providers as providers_pkg
providers_dir_list = providers_pkg.__path__
print('Providers dir list:', providers_dir_list)

for module_info in pkgutil.iter_modules(providers_dir_list):
    module_name = module_info.name
    full_module_name = f'fwsort.signals.providers.{module_name}'
    print(f'Checking module: {full_module_name}')
    if full_module_name in sys.modules:
        try:
            importlib.reload(sys.modules[full_module_name])
            print(f'  Reloaded: {full_module_name}')
        except Exception as e:
            print(f'  Failed: {e}')

# 3. Clear registries
_PROVIDERS.clear()
_PROVIDER_INSTANCES.clear()
_PROVIDER_CATEGORIES.clear()

# 4. Rediscover
_discover_providers()
print('After rediscover:', list(_PROVIDERS.keys()))