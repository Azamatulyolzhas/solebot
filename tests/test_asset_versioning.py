"""Cache-busting rewriter contract tests."""
from asset_versioning import add_cache_bust


class TestAddCacheBust:

    def test_rewrites_local_js(self):
        html = '<script src="/dashboard/static/app.js"></script>'
        out = add_cache_bust(html, build_id="abc1234")
        assert 'src="/dashboard/static/app.js?v=abc1234"' in out

    def test_rewrites_local_css(self):
        html = '<link rel="stylesheet" href="/dashboard/static/styles.css">'
        out = add_cache_bust(html, build_id="abc1234")
        assert 'href="/dashboard/static/styles.css?v=abc1234"' in out

    def test_preserves_external_urls(self):
        html = '''
            <script src="https://cdn.example.com/lib.js"></script>
            <link href="//fonts.googleapis.com/css2?family=Inter" rel="stylesheet">
        '''
        out = add_cache_bust(html, build_id="x")
        assert "?v=x" not in out

    def test_does_not_double_version_already_versioned(self):
        html = '<script src="/app.js?v=old"></script>'
        out = add_cache_bust(html, build_id="new")
        assert "?v=new" not in out
        assert 'src="/app.js?v=old"' in out

    def test_appends_with_ampersand_when_existing_query(self):
        html = '<script src="/app.js?cb=1"></script>'
        out = add_cache_bust(html, build_id="x")
        assert 'src="/app.js?cb=1&v=x"' in out

    def test_multiple_assets_in_one_html(self):
        html = '''
            <link rel="stylesheet" href="/shared/static/tokens.css">
            <link rel="stylesheet" href="/dashboard/static/styles.css">
            <script src="/dashboard/static/app.js"></script>
        '''
        out = add_cache_bust(html, build_id="42")
        assert out.count("?v=42") == 3

    def test_handles_no_assets(self):
        html = "<html><body>hi</body></html>"
        assert add_cache_bust(html, build_id="x") == html


class TestBuildIdLooksReasonable:

    def test_build_id_non_empty(self):
        from asset_versioning import BUILD_ID
        assert BUILD_ID
        assert isinstance(BUILD_ID, str)
