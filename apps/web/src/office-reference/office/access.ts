import { AccessState, OfficeEntity } from './types';

export function resolveEntityAccessState(entity: OfficeEntity): AccessState | undefined {
    return entity.accessState ?? entity.accessPolicy?.state ?? entity.door?.currentState;
}
