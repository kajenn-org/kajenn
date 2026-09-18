kajenn documentation
====================

.. raw:: html

   <div class="brand-lockup">
     <img class="brand-light" src="_static/branding/kajenn-logo.png" alt="kajenn" width="190">
     <img class="brand-dark" src="_static/branding/kajenn-logo-dark.png" alt="kajenn" width="190">
   </div>


*A spicy ASGI application server.*

**kajenn** is an ASGI server with the features FastAPI leaves to the user —
sessions, authentication, websocket channels, tasks, MCP, storage — and a base
server application included. Based on genropy history and genro-modules.

One instance-isolated server mounts your applications and routes requests
through `genro-routes <https://pypi.org/project/genro-routes/>`_. No globals, no
module state: the server is an object you build, run, and throw away.

New here? Start with :doc:`getting-started`, then :doc:`configuration`.
Coming from another ASGI framework? Read :doc:`coming-from-fastapi`.
For the whole machine in one page, read :doc:`architecture/overview`.

These pages describe the development checkout. Source and release packages may
differ; see :doc:`building` for local builds and publication status.

Multiworker orchestration is not part of this package: it lives in the separate
``kajenn-orchestra`` distribution, at
https://github.com/kajenn-org/kajenn-orchestra.

.. toctree::
   :maxdepth: 2
   :caption: Getting started

   getting-started
   configuration
   coming-from-fastapi
   building

.. toctree::
   :maxdepth: 2
   :caption: Concepts

   concepts

.. toctree::
   :maxdepth: 2
   :caption: Guides

   guides/index

.. toctree::
   :maxdepth: 2
   :caption: Architecture

   architecture/overview

.. toctree::
   :maxdepth: 2
   :caption: API Reference

   api/index

.. toctree::
   :maxdepth: 1
   :caption: FAQ

   faq

.. toctree::
   :maxdepth: 1
   :caption: Design notes

   design/channel-protocol
   design/channel-benchmark

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
