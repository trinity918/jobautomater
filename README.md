# JobAutomater

A personal job-search agent that runs on your own Windows PC. It reads your resume, collects fresh roles from free
job APIs (India and remote worldwide), ranks them against you, fills each employer's own application form in a real
browser you can watch, and tracks what the company says back.

One press of **Start** finds today's roles you haven't applied to; one press of **Apply** sends them.

- **Sources (free, no key):** Greenhouse, Lever, Ashby, SmartRecruiters, Workable and Workday boards of ~180
  companies, plus Remotive, Remote OK, Arbeitnow, Jobicy, Himalayas, Working Nomads and We Work Remotely.
  Adzuna, Jooble and JSearch switch on with free keys.
- **Ranking:** skills, title, location, level and freshness, plus an optional AI re-rank.
- **Applying:** Playwright drives your installed Chrome or Edge. It fills text fields, dropdowns, checkboxes, Yes/No
  buttons and uploads, and pauses for captchas and sign-ins. A live view lets you watch and take over.
- **AI (optional):** any OpenAI-compatible API, such as xAI Grok, Groq (free), OpenRouter or Ollama (local). It
  answers open questions, writes cover letters and upgrades your resume.
- **Tracker:** reads company replies from Gmail (read-only IMAP) and moves each application to
  confirmed / assessment / interview / rejected.

## Requirements

- Windows 10/11
- Python 3.10 or newer (<https://www.python.org/downloads/>, tick "Add to PATH")
- Google Chrome or Microsoft Edge (Edge is already on every Windows PC)

## Install

```
git clone https://github.com/trinity918/jobautomater.git
cd jobautomater
setup.bat
```

`setup.bat` creates a virtual environment, installs the packages, and creates your private files from the templates.

## Run

Double-click `run.bat`, or from a terminal in the folder:

```
.\run.bat
```

The dashboard opens at <http://127.0.0.1:8765>. Keep the window open while you use it; Ctrl+C stops it.

## First-time setup (about 5 minutes)

1. **Resume tab:** upload your resume (PDF or DOCX). Your name, contacts, city, education, CGPA and current role are
   copied from it.
2. **`info.txt`:** this file in the project folder is created on first run. Put anything the resume doesn't have
   there, one `Key: value` per line: address, PIN code, date of birth, 10th/12th marks, expected CTC and an "About me"
   paragraph. Whatever is in `info.txt` wins over everything else. Extra lines such as `Father's name: …` are used
   when a form asks that question. You can also edit it in the **Profile** tab.
3. **Sign in to Google once:** press the button on the Today screen (or Settings → Sign in to sites). A normal browser
   window opens on the app's own profile; sign in, then close it. After that, the app presses "Continue with Google"
   on career sites for you. LinkedIn and other sites work the same way.
4. **Optional keys in `.env`:** see below.
5. **Today tab:** press **Start**, check the list, then press **Apply**.

## Optional keys (`.env`)

All of these are optional. Without any keys, the app ranks jobs and fills forms with built-in rules.

| What | Lines in `.env` |
| --- | --- |
| AI via Groq (free) | `LLM_PROVIDER=groq`, `LLM_API_KEY=` (from <https://console.groq.com>), `LLM_MODEL=openai/gpt-oss-120b` |
| AI via xAI Grok | `LLM_PROVIDER=xai`, `LLM_API_KEY=` (from <https://console.x.ai>), `LLM_MODEL=` a current Grok model |
| AI via Ollama (local) | `LLM_PROVIDER=ollama`, `LLM_MODEL=qwen2.5:7b` |
| Tracker (Gmail) | `IMAP_USER=` your Gmail, `IMAP_PASSWORD=` an App Password from <https://myaccount.google.com/apppasswords> |
| More job sources | `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `JOOBLE_API_KEY`, `RAPIDAPI_KEY` (JSearch) |

Restart the app after editing `.env`. **Settings → Test** checks the AI key.

## Tabs

| Tab | What it is for |
| --- | --- |
| Today | Start → today's roles you haven't applied to → Apply. "Submit for me" sends them; "Let me check each" fills them and waits for you. |
| Jobs | Every role, with search, filters (fresher/experienced, startup/MNC, remote) and the match score with reasons. |
| Applications | Every application: status, timeline, screenshots of the filled form and the confirmation, and the answers sent. |
| Tracker | Company emails matched to your applications, as an Inbox and a Pipeline. |
| Live | The browsers applying right now. **Take over** lets you click and type (captchas, sign-ins). |
| Resume | Edit any line, pick one of 8 formats, **Upgrade** with AI, and **Save and use** to make the PDF that gets uploaded. |
| Profile | `info.txt` and every detail used on forms. Blank important fields are highlighted. |
| Settings | Auto-apply, apply mode, daily cap, minimum score, sign-ins, sources, AI model, inbox. |

## Your data stays on your PC

These files hold your personal details and are **not** in git (see `.gitignore`):

| File | Holds |
| --- | --- |
| `.env` | API keys and the Gmail app password (made from `.env.example`) |
| `info.txt` | Your master details (made on first run) |
| `config/profile.yaml` | Details read from your resume (made from `config/profile.example.yaml`) |
| `config/settings.yaml` | Your settings (defaults apply when it is missing) |
| `data/` | The database, your resumes, screenshots and the browser profile with your sign-ins |

## Safety rails

- A daily cap (25 by default).
- At most one application per company every 30 days.
- 20–60 second pauses between applications.
- Sensitive questions (criminal record, health, ID numbers) always come to you.
- Captchas and sign-ins pause the browser for you.
- Workday and SmartRecruiters roles are "assisted": the app fills the form, and you sign in or press Submit in the
  Live tab. LinkedIn and Naukri applications are not automated.

## Project layout

```
app/
  main.py            FastAPI server and API
  discovery.py       fetches jobs from every source
  sources/           ATS boards (ats.py) and job boards (aggregators.py)
  ranker.py          match scoring
  resume.py          resume reading; resume_render.py: the 8 formats and PDF export
  info.py            info.txt reader; profile_fill.py: resume -> profile
  apply/             browser sessions, form filler, answer rules, Google sign-in, apply engine
  inbox.py           Gmail tracker
  llm.py             OpenAI-compatible client (Grok, Groq, OpenRouter, Ollama)
web/                 the dashboard (Alpine.js, no build step)
config/              companies.yaml, profile.example.yaml
tools/               build_companies.py: checks and adds company careers boards
```

Add companies to `config/companies.yaml`, then run `.venv\Scripts\python tools\build_companies.py` to check them.

## Notes

- Windows Smart App Control can block unsigned native code, so the app uses your installed Chrome/Edge and pure-Python
  ranking. Playwright's own Chromium is only a fallback.
- Use it responsibly: apply only to roles you want, and check the answers in the Applications tab.
