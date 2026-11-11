# Return Points

A phone-friendly app for Sleeper fantasy football leagues that scores kick and
punt return yardage, projects it for the coming week, and shows which free
agents would improve your lineup once return points are counted.

It has two parts:

- `build_data.py` runs every morning on GitHub (free) and saves a small file of
  kickoff and punt plays, the schedule, and a player-ID table in `data/`.
- `app.py` is the Streamlit web app. It reads those files, calls Sleeper live
  for your league, roster and projections, and shows the results.

## Setup (about 20 minutes, once)

Do steps 1 to 3 on a computer if you can. Uploading files is much easier there.

### 1. Put the files on GitHub
1. Create a free account at github.com and click **New repository**. Name it
   something like `fantasy-returns`. Public is simplest. Leave the README box unchecked.
2. Unzip this project. On the repository page click **Add file → Upload files**
   and drag in `app.py`, `core.py`, `build_data.py`, `requirements.txt`,
   `README.md`, and the `data` folder. Click **Commit changes**.
3. The hidden `.github` folder often doesn't upload with drag and drop. Instead
   click **Add file → Create new file**, type
   `.github/workflows/refresh.yml` as the file name (typing the slashes
   creates the folders), paste in the contents of that file from the zip, and commit.

### 2. Load the data the first time
1. Open the repository's **Actions** tab. If asked, click the green button to
   enable workflows.
2. Choose **Refresh NFL data** on the left, then **Run workflow**. After a
   minute or two it should show a green check, and a `data` folder with four
   files appears in the repository.
3. If it fails with a permissions error, go to **Settings → Actions → General →
   Workflow permissions**, choose **Read and write permissions**, save, and run it again.

After this it refreshes itself every morning.

### 3. Deploy the app
1. Go to share.streamlit.io and sign in with GitHub.
2. Click **Create app**, pick your repository, branch `main`, main file `app.py`, and click **Deploy**.
3. The first build takes a few minutes. You'll get a link like `yourname-fantasy-returns.streamlit.app`.

### 4. Install it on your Galaxy S24 Ultra
1. Open the link in Chrome.
2. Tap the **⋮** menu → **Add to Home screen** (or **Install app** if you see it).
3. Confirm. It now opens from your home screen like an app.

## Using it
- **Lineup & waivers**: your best lineup this week, and free agents who would improve it.
- **Projections**: projected return points for every returner.
- **Season so far**: return points each player has scored this season.
- **Model check**: how the projections have done against what actually happened.

The league ID, username and team name at the top of `app.py` are the defaults.
You can also change them in the app under **League settings**. If the repository
is public, those two values are visible to anyone who finds it.

## If something goes wrong
- **"No data files yet"**: run the **Refresh NFL data** action (step 2).
- **Slow first open**: free Streamlit apps sleep when unused. Waiting 30 to
  60 seconds wakes it. Tap **Refresh** in the app if numbers look stale.
- **Offensive points say "season-to-date averages"**: Sleeper's projections
  endpoint isn't official. When it isn't available the app falls back to averages.
- **Wrong or missing player**: the bottom of the **Season so far** tab lists any returners
  that couldn't be matched to Sleeper.

## Changing the model
All the logic is in `core.py`. The constants near the top of the projection
section (`N_ROLE`, `N_TEAM`, `OPP_SHRINK`, and so on) control how much weight
recent games and opponent strength get.
