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

"""Tasks package: the file spool of batch tasks (folder-move model, ◆D22)."""

from .executor import WORKER_ID, LocalTaskExecutor
from .hub import EventHub
from .manager import TaskManager
from .mixin import TaskGrammar, TaskMixin
from .schedule import AtSpec, CronSpec, EverySpec, TaskCadence
from .scheduler import TaskScheduler
from .spool import STATUSES, TaskSpool, new_descriptor
from .store import FileTaskStore, TaskStore

__all__ = [
    "STATUSES",
    "WORKER_ID",
    "AtSpec",
    "CronSpec",
    "EventHub",
    "EverySpec",
    "FileTaskStore",
    "LocalTaskExecutor",
    "TaskCadence",
    "TaskGrammar",
    "TaskManager",
    "TaskMixin",
    "TaskScheduler",
    "TaskSpool",
    "TaskStore",
    "new_descriptor",
]
