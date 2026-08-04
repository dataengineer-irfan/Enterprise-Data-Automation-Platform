from pathlib import Path
import tempfile
from agent.manager import ManagerAgent
from agent.plan_memory import PlanMemory
from agent.shared_storage import SharedStorage
from core.audit import AuditLog
from tests.test_phase4_orchestration import FakeLLMProvider

root = Path('.').resolve()
manager = ManagerAgent(
    llm=FakeLLMProvider({'intent':'multi_table_job','tables':['p_dtl_tb','p_alt_id_tb'],'reasoning':'multi','clarifying_question':None}),
    config_dir=root/'config',
    ddl_dir=root/'input'/'ddl',
    plan_memory=PlanMemory(tempfile.mkdtemp()),
    audit_log=AuditLog(Path(tempfile.mkdtemp())/'audit.jsonl'),
    shared_storage=SharedStorage(tempfile.mkdtemp()),
)
print('tables', len(manager.list_table_names()))
print('ok')
