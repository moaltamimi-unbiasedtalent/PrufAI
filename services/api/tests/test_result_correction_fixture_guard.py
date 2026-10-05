import uuid

import pytest

from app.models import OrganisationMembership
from tests.integration_support.result_correction_scenario import _flush_fixture_group


def test_fixture_flush_guard_rejects_dangling_fk_before_flush(session) -> None:
    membership = OrganisationMembership(
        organisation_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    with pytest.raises(AssertionError, match="OrganisationMembership.organisation_id"):
        _flush_fixture_group(session, membership)

    assert membership not in session
