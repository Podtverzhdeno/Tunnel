"""Create a private local bridge key and the matching extension configuration."""
import json
from bridge import initialize,PORT,ROOT
if __name__=="__main__":
    initialize()
    print(json.dumps({"ok":True,"bridge_config":"web-companion/bridge-config.json","port":PORT,
                      "note":"Reload the unpacked extension after changing the manifest."}))
