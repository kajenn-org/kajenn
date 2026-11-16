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

The dedicated process of an application mounted with ``spawner="subprocess"``
mounts none. Login, users and tokens are not here: they belong to the
authentication application (see :doc:`auth`). The management pages themselves
are not here either: this package serves the JSON a page drives.

Application and grammar
-----------------------

.. autoclass:: kajenn.server_app.server_app.ServerApplication
   :members: declared_tags, sections, attach_section

.. autoclass:: kajenn.server_app.server_app.ServerApplicationGrammar
   :members: security_txt, robots, agent

Sections
--------

.. automodule:: kajenn.server_app.server_sections.tasks_section
   :members:
   :show-inheritance:

.. automodule:: kajenn.server_app.server_sections.monitor_section
   :members:
   :show-inheritance:
