kajenn documentation
====================

.. raw:: html

   <div class="brand-lockup">
     <img class="brand-light" src="_static/branding/kajenn-logo.png" alt="kajenn" width="190">
     <img class="brand-dark" src="_static/branding/kajenn-logo-dark.png" alt="kajenn" width="190">
   </div>


*A spicy ASGI application server.*

**kajenn** is an ASGI application server with the features FastAPI leaves to the user:
sessions, authentication, websocket channels, background tasks, MCP and
storage, plus a base server application included. One instance-isolated server
mounts your applications and routes requests through `genro-routes
<https://pypi.org/project/genro-routes/>`_. No globals, no module state — the
server is an object you build, run, and throw away. Based on genropy history
and genro-modules.

New here? Start with :doc:`getting-started`. Coming from another ASGI framework?
Read :doc:`coming-from-fastapi`. For common questions, see :doc:`faq`.

These pages describe the development checkout. Source and release packages may
differ; see :doc:`building` for local builds and publication status.

Multiworker orchestration lives in the separate ``kajenn-orchestra`` package,
and the Django adapter in ``kajenn-django``; each has documentation of its own.

.. toctree::
   :maxdepth: 2
   :caption: Getting started

   getting-started
   concepts
   coming-from-fastapi
   faq
   building

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

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
