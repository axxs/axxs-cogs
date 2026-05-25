from whatsonin.whatsonin import clean_description, parse_command_args


def test_clean_description_strips_html_tags():
    assert clean_description("<p>Hello <b>world</b></p>") == "Hello world"


def test_clean_description_collapses_whitespace():
    assert clean_description("Hello\n\n  world") == "Hello world"


def test_clean_description_handles_empty_input():
    assert clean_description("") == ""
    assert clean_description(None) == ""


def test_parse_placename_only():
    placename, limit, days = parse_command_args("hobart")
    assert placename == "hobart"
    assert limit == 0
    assert days == 0


def test_parse_with_flags():
    placename, limit, days = parse_command_args("launceston --days 14 --limit 5")
    assert placename == "launceston"
    assert limit == 5
    assert days == 14


def test_parse_flag_equals_form():
    placename, limit, days = parse_command_args("devonport --limit=3 --days=7")
    assert placename == "devonport"
    assert limit == 3
    assert days == 7
