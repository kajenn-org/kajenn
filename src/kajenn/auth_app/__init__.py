# Copyright 2025 Softwell S.r.l.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The authentication application package.

It holds the store of users, groups, tags and tokens (`AuthStore`, with its
file backend `FileAuthStore`, in `auth_store`). It is the subpackage
``kajenn.auth_app``: ``import kajenn`` does not load it.
"""

from .auth_store import AuthStore, FileAuthStore

__all__ = ["AuthStore", "FileAuthStore"]
