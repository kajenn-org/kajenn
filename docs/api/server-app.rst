Server application
==================

The ``kajenn.server_app`` package: the system surface a server exposes under
``/_server``, a subpackage of the core. ``AsgiServer`` mounts it with the code
``_server`` when no declared application has that code; an application declared
with the code ``_server`` takes its place::

    from kajenn import AsgiServer
    from kajenn.server_app import ServerApplication

    class MyServerApp(ServerApplication):
        ...

    server = AsgiServer(applications=[MyServerApp, (MyApp, {"mount": ""})])

The role process of an external application mounts none. The management pages
themselves are not here: this package serves the JSON a page drives.

Application and grammar
-----------------------

.. autoclass:: kajenn.server_app.server_app.ServerApplication
   :members: login_policy, oidc_providers, sections, auth_section, attach_section,
             register_auth_method, read_declared_login_surface

.. autoclass:: kajenn.server_app.server_app.ServerApplicationGrammar
   :members: login, oidc, provider

Login methods
-------------

.. automodule:: kajenn.server_app.auth_method
   :members:
   :show-inheritance:

.. automodule:: kajenn.server_app.oidc_method
   :members:
   :show-inheritance:

Sections
--------

.. automodule:: kajenn.server_app.server_sections.auth_section
   :members:
   :show-inheritance:

.. automodule:: kajenn.server_app.server_sections.users_section
   :members:
   :show-inheritance:

.. automodule:: kajenn.server_app.server_sections.tokens_section
   :members:
   :show-inheritance:

.. automodule:: kajenn.server_app.server_sections.tasks_section
   :members:
   :show-inheritance:

.. automodule:: kajenn.server_app.server_sections.monitor_section
   :members:
   :show-inheritance:
