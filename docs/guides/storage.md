# Storage

Prerequisites: [configuration](../configuration.md) and
[request handlers](requests.md). Storage is the server's file API. A named mount
such as `files:` identifies a backend and location; it does not publish an HTTP
route. `server.storage` is a genro-storage StorageManager.

## Write and read a file

Create an empty writable directory and run the example there. Save as `files.py`.
It creates only its demo `data` directory; the storage backend requires mount
anchors to exist before server construction.

```python
from pathlib import Path
from genro_routes import route
from kajenn import AsgiServer, RoutedApplication


class Files(RoutedApplication):
    mount = ""

    @route()
    def save(self, text: str = "hello") -> dict:
        node = self.server.storage.node("files:note.txt")
        node.write_text(text)
        return {"text": node.read_text()}

    @route()
    def read(self) -> dict:
        return {"text": self.server.storage.node("files:note.txt").read_text()}


if __name__ == "__main__":
    directory = Path("data").resolve()
    directory.mkdir(exist_ok=True)
    server = AsgiServer(
        applications=[Files],
        storage=[{"name": "files", "protocol": "local", "base_path": str(directory)}],
    )
    server.serve(host="127.0.0.1", port=8000)
```

```bash
python files.py
# In a second terminal:
curl -X POST 'http://127.0.0.1:8000/save?text=hello'
curl http://127.0.0.1:8000/read
```

Both calls return `{"text":"hello"}`; `data/note.txt` contains `hello`.
Call save before read. Stop with Ctrl-C; remove the demo directory only when its
contents are no longer needed. These public routes are local demonstration
endpoints: add authentication and operation-specific policy for a real service.

## Default mounts and synchronous work

Without custom mounts, `site:` points to the deployment directory and `home:`
to the configured site home (or the same directory when no home is declared).
Named paths make application code independent of backend locations.
See [site homes](cli.md#the-site-home).

kajenn's storage interface is synchronous. The example uses synchronous route
handlers, which the routed dispatcher runs through the server's worker pool.
Inside an async handler, put blocking storage work in a synchronous helper and
await `self.server.run_sync(helper)`. Do not assume a storage operation is
nonblocking merely because its caller is async.

## Optional encryption

Credential stores write encrypted records and require key material. In a shell,
generate a local test key with the cryptography package installed by kajenn:

```bash
export DEMO_STORAGE_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
```

This standalone check writes encrypted bytes and reads the original text:

```python
import os
from kajenn import AsgiServer

server = AsgiServer(storage_key=os.environ["DEMO_STORAGE_KEY"])
node = server.storage.node("site:encrypted-demo.txt")
node.write_text("private demo", encrypted=True)
assert node.read_text() == "private demo"
```

Keep the same key to reopen persisted data; generating a replacement key does not
re-encrypt existing files. Without key material, an encrypted write fails.
Encryption does not authorize HTTP access: route permissions remain separate.

```{admonition} In revisione
:class: warning

This guide verifies local storage and encryption. Cloud-backend credentials,
optional dependencies and deployment-specific key rotation are not covered by
this walkthrough and still require integration verification.
```
