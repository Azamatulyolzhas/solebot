"""Unit tests for pure order-flow helpers in orders.py."""
import orders


class TestLooksLikeOrderRequest:
    def test_triggers(self):
        assert orders.looks_like_order_request("хочу купить")
        assert orders.looks_like_order_request("Хочу заказать кроссовки 42")
        assert orders.looks_like_order_request("давайте оформить заказ")

    def test_non_triggers(self):
        assert not orders.looks_like_order_request("просто смотрю")
        assert not orders.looks_like_order_request("сколько стоит?")

    def test_natural_buy_verbs_start_order(self):
        # The production gap: 'оформите …' was ignored and fell through to a catalog
        # dump. These natural buy phrasings must start the order deterministically.
        assert orders.looks_like_order_request("оформите стан смив 44 размера")
        assert orders.looks_like_order_request("беру найк 43")
        assert orders.looks_like_order_request("возьму")
        assert orders.looks_like_order_request("закажите адидас")

    def test_negated_buy_verb_is_not_order(self):
        assert not orders.looks_like_order_request("не беру")
        assert not orders.looks_like_order_request("не хочу купить")

    def test_buy_verb_not_matched_inside_other_word(self):
        assert not orders.looks_like_order_request("я сам выберу размер")
        assert not orders.looks_like_order_request("где купить можно")


class TestLooksLikePhone:
    def test_valid_phones(self):
        assert orders.looks_like_phone("+7 701 234 56 78")
        assert orders.looks_like_phone("87012345678")
        assert orders.looks_like_phone("+1 (234) 567-8901")

    def test_invalid_phones(self):
        assert not orders.looks_like_phone("12345")          # too short
        assert not orders.looks_like_phone("Иван")            # no digits
        assert not orders.looks_like_phone("1234567890123456")  # too long (16)


class TestNormalizeProductInterest:
    def test_strips_generic_trigger_prefix(self):
        assert orders._normalize_product_interest("хочу купить найк аир") == "найк аир"

    def test_generic_only_returns_empty(self):
        assert orders._normalize_product_interest("хочу купить") == ""
        assert orders._normalize_product_interest("оформить заказ") == ""

    def test_keeps_non_trigger_text(self):
        assert orders._normalize_product_interest("красные кроссовки") == "красные кроссовки"

    def test_strips_mid_sentence_trigger(self):
        # The production bug: a trigger NOT at the start stored the whole frustrated
        # sentence as the product. Now we keep only what follows the trigger.
        assert (
            orders._normalize_product_interest("я же сказал хочу купить найк айр форсы")
            == "найк айр форсы"
        )

    def test_mid_sentence_trigger_with_no_product_is_empty(self):
        assert orders._normalize_product_interest("ну ладно давайте хочу купить") == ""

    def test_strips_imperative_trigger(self):
        assert (
            orders._normalize_product_interest("оформите стан смив 44 размера")
            == "стан смив 44 размера"
        )
        assert orders._normalize_product_interest("беру найк 43") == "найк 43"

    def test_bare_size_after_trigger_is_empty(self):
        # 'беру 44' refines the current product (a size), it doesn't name one — so
        # the flow asks which product rather than binding the order to '44'.
        assert orders._normalize_product_interest("беру 44") == ""

    def test_trigger_then_only_filler_is_empty(self):
        assert orders._normalize_product_interest("оформите заказ пожалуйста") == ""

    def test_trigger_then_demonstrative_is_empty(self):
        # 'оформим этот' → resolve the pick from context, don't store 'этот' as Товар.
        assert orders.looks_like_order_request("давайте оформим этот")
        assert orders._normalize_product_interest("давайте оформим этот") == ""


class TestOrderStatusValidation:
    def test_known_statuses(self):
        assert "new" in orders.ORDER_STATUSES
        assert "cancelled" in orders.ORDER_STATUSES


class TestNormalizePhone:
    def test_kz_11_digits(self):
        assert orders.normalize_phone("87012345678") == "+77012345678"
        assert orders.normalize_phone("+7 701 234 56 78") == "+77012345678"

    def test_kz_10_digit_national(self):
        assert orders.normalize_phone("7012345678") == "+77012345678"

    def test_rejects_random_10_digits(self):
        # The transcript bug: a junk 10-digit number must not pass.
        assert orders.normalize_phone("5676678657") is None

    def test_rejects_non_phone(self):
        assert orders.normalize_phone("Иван") is None
        assert orders.normalize_phone("12345") is None


class TestIsValidName:
    def test_rejects_pure_digits(self):
        # The transcript bug: a phone number typed into the name slot.
        assert not orders._is_valid_name("87765645633")

    def test_rejects_too_short(self):
        assert not orders._is_valid_name("a")
        assert not orders._is_valid_name("")

    def test_accepts_real_names(self):
        assert orders._is_valid_name("Иван")
        assert orders._is_valid_name("Ali")


class TestConfirmCancel:
    def test_confirm_words(self):
        assert orders._is_confirm("да")
        assert orders._is_confirm("Да, верно")
        assert orders._is_confirm("оформляй")

    def test_cancel_words(self):
        assert orders._is_cancel("нет")
        assert orders._is_cancel("не надо")
        assert orders._is_cancel("отмена")
        assert orders._is_cancel("Нет, спасибо")  # bare cancel + filler

    def test_correction_is_not_cancel(self):
        # 'нет' + a change request must NOT hard-cancel the order (it lost the
        # customer's progress when they only wanted a different size/colour).
        assert not orders._is_cancel("нет размер 41")
        assert not orders._is_cancel("нет, другой цвет")
        assert not orders._is_cancel("нет хочу 42")

    def test_ambiguous_is_neither(self):
        assert not orders._is_confirm("привет")
        assert not orders._is_cancel("привет")
