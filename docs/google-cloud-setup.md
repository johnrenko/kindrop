# Google Cloud setup

Kindrop needs one OAuth client for Drive read access and Gmail send/read access. It does not need a paid Google Cloud service.

## 1. Create or choose a project

Open the [Google Cloud Console](https://console.cloud.google.com/), select a project, then open **APIs & Services**.

## 2. Enable APIs

In **Library**, enable:

- Google Drive API
- Gmail API

## 3. Configure the OAuth consent screen

Open **OAuth consent screen** (or **Google Auth Platform** in the current console UI):

1. Choose **External** unless the account belongs to a Google Workspace organization that should own the app.
2. Give the app a name such as `Kindrop` and enter the required contact emails.
3. Keep the app in **Testing** status for personal use.
4. Add the Google account that will send to Kindle as a **Test user**.

Kindrop requests only these Google permissions:

- `drive.readonly`
- `gmail.send`
- `gmail.readonly`
- OpenID email identity, used to show which account is connected

Google may describe these as sensitive scopes. A private testing app used only by its listed test user does not need public verification.

## 4. Create the OAuth client

Open **Credentials**, choose **Create credentials → OAuth client ID**, and select **Desktop app**. Download the JSON file.

In Kindrop, open **Settings**, choose the JSON under **Google connection**, and then select **Connect Google**. The browser returns to:

```text
http://127.0.0.1:8787/api/oauth/callback
```

The client JSON and OAuth refresh token are encrypted locally. Kindrop never needs your Google password.

## 5. Authorize the sender in Amazon

In Amazon's **Manage Your Content and Devices → Preferences → Personal Document Settings**, add the connected Gmail address to the approved sender list. Copy the device's Send to Kindle address into Kindrop Settings.

If Google reports that the app is unavailable, confirm that the consent screen remains in Testing and that the connected account is listed as a test user. If OAuth credentials are replaced, disconnect Google in Kindrop and upload the new JSON before reconnecting.
