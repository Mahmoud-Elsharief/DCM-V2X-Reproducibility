from nrv2x import (
    published_required_prb, published_required_subchannels,
    resource_sizing,
)


def test_published_anchor_values():
    assert published_required_prb(4, 190) == 25
    assert published_required_prb(4, 350) == 44
    assert published_required_prb(11, 190) == 12
    assert published_required_prb(11, 350) == 20


def test_paper_300_byte_widths_are_four_and_two_subchannels():
    assert published_required_prb(4, 300) == 39
    assert published_required_prb(11, 300) == 18
    assert published_required_subchannels(4, 300, 10) == 4
    assert published_required_subchannels(11, 300, 10) == 2


def test_legacy_widths_remain_available_for_result_bridge():
    assert resource_sizing("legacy", 4, 300).required_subchannels == 2
    assert resource_sizing("legacy", 11, 300).required_subchannels == 1


def test_published_interpolation_refuses_unvalidated_extrapolation():
    import pytest
    with pytest.raises(ValueError):
        published_required_prb(4, 500)


def test_fixed_common_widths():
    assert resource_sizing("fixed", 4, 150).required_subchannels == 2
    assert resource_sizing("fixed", 11, 150).required_subchannels == 1
    assert resource_sizing("fixed", 4, 300).required_subchannels == 2
    assert resource_sizing("fixed", 11, 300).required_subchannels == 1
