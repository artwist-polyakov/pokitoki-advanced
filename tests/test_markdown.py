import unittest

from bot import markdown

TEXT_MD = r"""You can easily regular expressions them using the `sqlean-regexp` extension.

> **Note**. Unlike other DBMS, adding extensions to SQLite is a breeze.

With `sqlean-regexp`, matching a string against a pattern becomes as easy as:

```sql
select count(*) from messages
where msg_text regexp '\d+';
```

`regexp_like(source, pattern)` checks if the source string matches the pattern:

```sql
select regexp_like('Meet me at 10:30', '\d+:\d+');
select 10 > 5 = true;
```

See [Documentation](https://github.com/nalgeon/sqlean) for reference.
"""

TEXT_HTML = r"""You can easily regular expressions them using the <code>sqlean-regexp</code> extension.

&gt; <b>Note</b>. Unlike other DBMS, adding extensions to SQLite is a breeze.

With <code>sqlean-regexp</code>, matching a string against a pattern becomes as easy as:

<pre>
select count(*) from messages
where msg_text regexp '\d+';
</pre>

<code>regexp_like(source, pattern)</code> checks if the source string matches the pattern:

<pre>
select regexp_like('Meet me at 10:30', '\d+:\d+');
select 10 &gt; 5 = true;
</pre>

See [Documentation](https://github.com/nalgeon/sqlean) for reference.
"""


class Test(unittest.TestCase):
    def test_to_html(self):
        text = markdown.to_html(TEXT_MD)
        self.assertEqual(text, TEXT_HTML)

    def test_ticks(self):
        text = markdown.to_html("one `two` three")
        self.assertEqual(text, "one <code>two</code> three")
        text = markdown.to_html("one `two three")
        self.assertEqual(text, "one `two three")
        text = markdown.to_html("one `two\n` three")
        self.assertEqual(text, "one `two\n` three")

    def test_bold(self):
        text = markdown.to_html("one **two** three")
        self.assertEqual(text, "one <b>two</b> three")
        # malformed pattern should stay untouched
        text = markdown.to_html("**foo *bar** baz")
        self.assertEqual(text, "**foo *bar** baz")
        # inline code with asterisks should be preserved
        text = markdown.to_html("operator `**` is so ** powerful")
        self.assertEqual(text, "operator <code>**</code> is so ** powerful")

    def test_bullet(self):
        text = markdown.to_html("*   item1\n* item2")
        self.assertEqual(text, "— item1\n* item2")
