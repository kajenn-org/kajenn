Authentication
==============

Two packages. ``kajenn.auth`` is the server side: the auth mixin asks the
application mounted with code ``_auth`` for the identity of a presented
credential and of a session, and caches the answers. ``kajenn.auth_app`` is the
authentication application: ``AuthApplicationBase``, its login methods, its
management sections and the store of users, groups, tags and tokens.

Server side
-----------

.. automodule:: kajenn.auth.mixin
   :members:
   :show-inheritance:

Application and grammar
-----------------------

.. automodule:: kajenn.auth_app.auth_app
   :members:
   :show-inheritance:

Store
-----

.. automodule:: kajenn.auth_app.auth_store
   :members:
   :show-inheritance:

Login methods
-------------

.. automodule:: kajenn.auth_app.auth_method
   :members:
   :show-inheritance:

.. automodule:: kajenn.auth_app.oidc_method
   :members:
   :show-inheritance:

Management sections
-------------------

.. automodule:: kajenn.auth_app.auth_sections.users_section
   :members:
   :show-inheritance:

.. automodule:: kajenn.auth_app.auth_sections.groups_section
   :members:
   :show-inheritance:

.. automodule:: kajenn.auth_app.auth_sections.tags_section
   :members:
   :show-inheritance:

.. automodule:: kajenn.auth_app.auth_sections.tokens_section
   :members:
   :show-inheritance:
