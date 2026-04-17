# Resume Agent – Chrome Extension

Run **Evaluate fit** and **Tailor** from any job listing page. The extension talks to your local Resume Agent API.

## Setup

1. **Backend and Google (OAuth)**  
   - Start the API: `make api`.  
   - In the extension popup, click **Sign in with Google**. A new tab opens the API’s OAuth flow; sign in there. When you’re redirected back, the session cookie is set and the extension can use it. You can also sign in from the web app (http://localhost:3000)—same session. No `credentials.json` or `token.json` needed.

2. **CORS**  
   - After loading the extension, open `chrome://extensions`, find "Resume Agent", and copy the **Extension ID** (e.g. `abcdefghijklmnop`).  
   - In the project `.env` add that origin to CORS:
     ```env
     API_CORS_ORIGINS=http://localhost:3000,http://localhost:5173,chrome-extension://YOUR_EXTENSION_ID
     ```
   - Restart the API after changing `.env`.

3. **Load the extension**  
   - Open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and select the `chrome_extension` folder in this repo.

4. **Settings (optional)**  
   - Click the extension icon, then **Settings**.  
   - Set **API base URL** (default `http://localhost:8000`) and **Web app URL** (default `http://localhost:3000`).  
   - Optionally set **Resume Doc ID** if you want to override the API default.

## Use

1. Open any job listing page (e.g. LinkedIn, Indeed, company career page).
2. Click the **Resume Agent** extension icon.
3. **Evaluate fit** – Uses the current page URL as the job link, calls the API to evaluate fit, and shows score and recommendations in the popup.
4. **Tailor resume** – Opens the Resume Agent web app in a new tab with the current page URL as `job_url` so you can run the full tailor flow there.

## Requirements

- API running (e.g. on port 8000).
- **Signed in via the web app** at least once (Sign in with Google) so the API has your session. The extension reuses that session; no file-based auth needed.
