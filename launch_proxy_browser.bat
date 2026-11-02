@echo off
echo ===================================================================
echo   TikTok Proxy Browser Launcher (US Los Angeles Session)
echo ===================================================================
echo.
echo Launching clean Chrome instance with Webshare Proxy...
echo Proxy Gateway: http://p.webshare.io:80
echo Egress Location: Los Angeles, CA (198.23.243.226)
echo.
echo NOTE: When prompted by Chrome for proxy credentials:
echo   Username: bvgafxhc-0
echo   Password: b3j3wowo1icd
echo.
echo Once Chrome opens:
echo   1. Log in to TikTok (enter SMS/email code if asked)
echo   2. Open Cookie-Editor extension -> Export as JSON
echo   3. Paste into GitHub Secrets: TIKTOK_COOKIES
echo.
echo ===================================================================

set CHROME_PATH="C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist %CHROME_PATH% set CHROME_PATH="C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not exist %CHROME_PATH% set CHROME_PATH="%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"

%CHROME_PATH% --user-data-dir="%TEMP%\tiktok_proxy_profile" --proxy-server="http://p.webshare.io:80" "https://www.tiktok.com/login"
