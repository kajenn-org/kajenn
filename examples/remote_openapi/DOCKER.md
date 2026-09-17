# Run the remote OpenAPI application in Docker

The application is still `app.py:create_application`, unchanged from the UDS
and ordinary TCP examples. Only the runner and its placement differ:

```text
browser -> host frontend :18764 -> TCP 127.0.0.1:18765
                               -> Docker port mapping -> app container :8765
```

Run all commands below from the repository root. They use Docker Engine and
Compose, plus the host `.venv` already used by the ordinary PoC. The image
installs the current checkout's core package with published dependencies; no
host venv, source checkout, credentials, database or Docker socket is mounted.
The container runs Python 3.13 as UID 10001 and the runner is PID 1.

## Start

```sh
docker compose -f examples/remote_openapi/compose.yaml up --build -d --wait
```

The runner explicitly uses `--address tcp:0.0.0.0:8765
--allow-network-listener`. The flag only permits a network listener; default
listeners and all client destinations remain loopback-only. Compose publishes
`127.0.0.1:18765:8765`, so the host frontend uses its normal connect-only mode.
The GNRF link trusts the operator-configured network; this is not a public,
authenticated cross-host deployment.

In another terminal:

```sh
PYTHONPATH="$PWD/src:$PWD" .venv/bin/python -c 'from examples.remote_openapi.frontend import create_server; create_server("tcp:127.0.0.1:18765", own_process=False).serve(host="127.0.0.1", port=18764)'
```

Open [Swagger](http://127.0.0.1:18764/demo/_meta/docs) or
[hello](http://127.0.0.1:18764/demo/hello). Hello reports PID 1 inside the
container; `/health` belongs to the host frontend. Set `REMOTE_PORT` when
running Compose to choose another published port, and use it in the frontend.

## Repeat the live proof

```sh
PYTHONPATH="$PWD/src:$PWD" .venv/bin/python examples/remote_openapi/verify_docker.py --exercise-restart
```

The script locates only this Compose project's `app` service. It checks a
healthy Linux container running as UID 10001, hello/PID 1, a byte-identical 1 MiB
binary echo, concurrent requests, deliberate HTTP 400, anonymous HTTP 401,
Swagger/schema mount and an actual browser-protocol WSX call. With
`--exercise-restart`, it then stops only that app, verifies host `/health` 200
and remote `/demo/hello` 503, starts the same container again and verifies a new
process start time plus frontend reconnection. It restores the app in `finally`
after the stop checks; a Docker engine/start failure still needs operator action.
No in-flight call is replayed. Omit the flag for checks without stopping the app.

If using `docker compose -p YOUR_PROJECT`, pass the same name to the proof with
`--project YOUR_PROJECT`. Use `--base-url` if you change the frontend port.
The default project is `genro-opaque-docker-poc`.

## ARM Docker VM compatibility

On the tested Docker Desktop 4.38/Engine 27.5.1 ARM VM, importing the
cryptography 50.0.1 wheel exited 132 with SIGILL. A diagnostic with
`OPENSSL_armcap=0` succeeded. This is a runtime compatibility workaround,
not a core/application patch or a dependency downgrade. It selects the
[OpenSSL CPU-capability override](https://docs.openssl.org/master/man3/OPENSSL_armcap/)
inside the container and can reduce cryptographic acceleration.

Only if that issue occurs, start using the optional override:

```sh
docker compose -f examples/remote_openapi/compose.yaml \
  -f examples/remote_openapi/compose.arm-compat.yaml up --build -d --wait
```

Keep both files when recreating that deployment. The proof's stop/start uses
the existing container configuration, so it preserves the override. Nothing
changes in Docker's global settings or in the host environment. The base
Compose file does not force this workaround on other systems.

## Stop

Stop the host frontend with Ctrl-C; it does not stop the container. To remove
only this PoC's container and network:

```sh
docker compose -f examples/remote_openapi/compose.yaml down
```

Include the same `-p YOUR_PROJECT` if you selected a custom project name. The
locally built image remains available; it has not been pushed to a registry.


## Recorded live result — 2026-09-08

The full proof passed on Docker Desktop 4.38.0 / Engine 27.5.1 (Linux arm64),
Compose 2.32.4, Python 3.13.15 and cryptography 50.0.1 in the container. This
machine needed the optional ARM override above. Host Python was 3.14.6.

The session used project `issue72-docker-20260908`, frontend
`http://127.0.0.1:18764`, and the default published TCP port 18765. Run the proof
with `--project issue72-docker-20260908` for that running deployment.

Verified: healthy non-root container, PID 1 application, HTTP and WSX, 1 MiB
byte equality, three concurrent calls, HTTP 400/401, Swagger external mount,
container stop yielding local 200/remote 503, and a new container process start
time followed by successful frontend reconnection. The two processes are left
running for interactive inspection. Logs are in the worktree's
`temp/issue72-docker-proof.log`; runtime image digest:
`sha256:fc8b002e317728dff0128533be262438576d42afe21dde1eff4fec97de86c0a1`.

The associated host regressions passed 27 tests across the new listener,
RemoteConnection and RemoteApplication suites. Ruff and diff checks passed.
The existing application code was not changed.

## Frame capacity and attention warnings

The Compose service passes the same transport variables used by the host
frontend. Export overrides in the shell **before** starting both services, e.g.:

```sh
export GNR_ASGI_FRAME_MAX_BYTES=268435456
export GNR_ASGI_FRAME_WARN_BYTES=1048576
export GNR_ASGI_FRAME_WARN_INTERVAL_SECONDS=60
```

The body limit follows the frame limit unless `GNR_ASGI_HTTP_MAX_BODY_BYTES`
is also exported. The complete frame includes HTTP and routing metadata, so its
maximum is not an exact maximum body size. Restart both sides after changing
policy. Accepted large frames generate throttled warnings without payload data;
small frames allocate no extra memory when the maximum is raised. See
[the transport contract](../../docs/internal/opaque_transport.md) for rejection
semantics and environment validation.
