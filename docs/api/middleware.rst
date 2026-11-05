Middleware
==========

The middleware base and mixin, and the shipped middleware: errors, logging,
CORS, session. Authentication is not a middleware since 0.4.0 — the execution
point authenticates on demand; see :doc:`../guides/authentication`. Hidden paths and well-known URLs are not a
middleware — they are the server's own demux rule: an application declares
its well-known paths in the class dict ``well_known``; see
:doc:`../guides/hidden-paths`.

.. automodule:: kajenn.middleware.base
   :members:
   :show-inheritance:

.. automodule:: kajenn.middleware
   :members: MiddlewareMixin, default_registry
   :show-inheritance:

.. automodule:: kajenn.middleware.errors
   :members:
   :show-inheritance:

.. automodule:: kajenn.middleware.logging
   :members:
   :show-inheritance:

.. automodule:: kajenn.middleware.cors
   :members:
   :show-inheritance:

.. automodule:: kajenn.middleware.session
   :members:
   :show-inheritance:
