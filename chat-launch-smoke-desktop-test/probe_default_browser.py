"""Read-only public association check; does not inspect browser profiles or cookies."""
import json, platform
result={"platform":platform.system(), "https_handler":None, "browser":"unknown"}
if platform.system()=="Windows":
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as k:
            value=winreg.QueryValueEx(k,"ProgId")[0]
            # Only classification, never expose association hashes or profile data.
            name=value.lower()
            if "yandex" in name or "yabrowser" in name:
                result.update(https_handler="Yandex",browser="Yandex Browser")
            elif "chrome" in name:
                result.update(https_handler="Chrome",browser="Chrome")
            elif "edge" in name or "microsoft" in name:
                result.update(https_handler="Edge",browser="Edge")
            else:
                result.update(https_handler="Other registered handler",browser="other")
    except OSError:
        pass
print(json.dumps(result))
