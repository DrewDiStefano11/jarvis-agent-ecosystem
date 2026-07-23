def recover_supervisor_state(db, supervisor_id: str):
    state = db.get_supervisor_state(supervisor_id)
    if not state:
        return

    instance_id = state['current_worker_instance_id']
    if instance_id:
        worker = db.get_worker_instance(instance_id)
        if worker and worker['status'] not in ('stopped', 'crashed', 'killed'):
            pass # Active worker, verify process existence next
