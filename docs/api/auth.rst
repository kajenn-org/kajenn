Authentication
==============

The auth mixin, the credential core and the identity stores. The core
authenticates by header credentials and by the session avatar; the login
methods that ask a human for a user and a password are in their own package —
see :doc:`server-app`.

.. automodule:: kajenn.auth.mixin
   :members:
   :show-inheritance:

.. automodule:: kajenn.auth.core
   :members:
   :show-inheritance:

.. automodule:: kajenn.auth.user_store
   :members:
   :show-inheritance:

.. automodule:: kajenn.auth.api_key_store
   :members:
   :show-inheritance:
