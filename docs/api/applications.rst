Applications
============

For working recipes, see :doc:`../guides/configuration-by-example`. The
management application lives in :doc:`server-app`.

SPA applications and pages
--------------------------

.. autoclass:: kajenn.spa_application.SpaApplicationBase
   :members: open_page, get_page, close_page, close_connection, send_page_message

.. autoclass:: kajenn.spa_page.Page
   :members: persistent, state, store, send_message

.. autoclass:: kajenn.spa_registry.SpaRegistry
   :members: new_store, release_page_instance, page_attached, page_user_changed, page_detached

OpenAPI
-------

.. automodule:: kajenn.applications.openapi
   :members:
   :show-inheritance:

MCP
---

.. automodule:: kajenn.applications.mcp
   :members:
   :show-inheritance:

.. automodule:: kajenn.mcp.engine
   :members:
   :show-inheritance:
