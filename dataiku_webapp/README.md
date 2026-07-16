# Dataiku Standard Webapp deployment

This is a Dataiku **Standard** webapp, deliberately not a managed-folder or dataset uploader. It only posts browser file bytes to the Python backend. The backend parses each request from memory, retains extracted records in process memory for 30 minutes, and drops raw file bytes immediately after parsing. Restarting the backend also removes all session data.

## Install

1. In the project code environment, install the packages in the repository `requirements.txt`, and make the `financereconai` package available as a project library or installed package.
2. In Dataiku DSS: **Code > Webapps > New web app > Standard**.
3. Copy `body.html`, `app.js`, and `style.css` into their matching editor tabs. Add a Python backend and paste `backend.py`.
4. Select the code environment, save, and start/restart the backend.

Do not make the webapp public for financial documents. DSS permission controls still apply to normal webapps; use project access controls and HTTPS. A Standard webapp can use Flask routes in its Python backend, as documented by Dataiku.

## Privacy boundary

There is no `dataiku.Folder`, dataset write, filesystem write, or managed-upload call in the backend. The browser holds its session UUID in `sessionStorage`; the backend store is per backend process and TTL-evicted. This provides temporary processing, not durable audit retention. For horizontally scaled backends, use sticky sessions or a secured TTL cache—never a Dataiku managed folder if the “not in Dataiku storage” constraint is absolute.
