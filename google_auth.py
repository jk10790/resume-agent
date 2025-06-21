import os.path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/documents'
]

def get_credentials():
    creds = None
    # token.json stores the user's access and refresh tokens.
    if os.path.exists('token.json'):
        print('token json exists')
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    # If there are no valid credentials, let user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print('creds expired, refreshing')
            creds.refresh(Request())
        else:
            print('No creds available, reading from credentials.json')
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            print('Done reading creds from credentials.json')
            creds = flow.run_local_server(port=0)
        # Save the credentials for next run
        with open('token.json', 'w') as token:
            print('Saving tokens to the token.json file')
            token.write(creds.to_json())
    return creds

if __name__ == "__main__":
    creds = get_credentials()
    print("✅ Authentication successful!")
