"""Isolated symbolic worker. Importing this module never imports SymPy or the app.

The executable installs an OS memory limit before importing SymPy. On Windows,
a Job Object limits committed memory and child processes; on POSIX RLIMIT_AS is
used. If the OS cannot install the limit, execution fails closed.
"""
from __future__ import annotations

import ast
import io
import json
import keyword
import logging
import math
import operator
import os
import re
import sys
import tokenize

logger = logging.getLogger("aiedu.symbolic")
# 允许的运算符。注意**没有** ``.``（属性访问）与 ``'``/``"``（字符串）。
_ALLOWED_OPS = frozenset({"+", "-", "*", "**", "/", "//", "%", "(", ")", "[", "]", ",", "=", ":", "<", ">", "<=", ">="})

_MAX_TEMPLATE_CHARS = 512
_MAX_TOKENS = 200
_MAX_AST_NODES = 400
# 整底数的指数上限：超过就拒解。64 已经远超任何真实题目（2**64 本身就不是题目答案），
# 而 9**9 这类「看起来无害」的写法一旦超过就会让 sympy 挂死。
_MAX_INT_EXPONENT = 64
_MAX_COUNT_OPS = 4000

# sympy 命名空间：只放数学构造函数，不放任何能触碰解释器的东西。
_SYMPY_NAMES = (
    "sqrt", "exp", "log", "ln", "sin", "cos", "tan", "cot", "sec", "csc",
    "asin", "acos", "atan", "acot", "asec", "acsc",
    "sinh", "cosh", "tanh", "asinh", "acosh", "atanh",
    "Abs", "sign", "floor", "ceiling", "Min", "Max", "Mod",
    "Rational", "Integer", "Float", "Symbol", "Pow",
    "pi", "E", "I", "oo", "zoo", "nan",
    "Eq", "Ne", "Lt", "Le", "Gt", "Ge", "And", "Or", "Not",
    "diff", "limit", "Integral", "Sum", "Product",
    "factorial", "binomial", "gcd", "lcm",
    "Piecewise", "FiniteSet", "Tuple", "Interval", "Union", "Intersection",
    "Matrix", "atan2", "root", "real_root", "conjugate", "re", "im",
)


def _sympy_namespace() -> dict:
    ns: dict = {"__builtins__": {}}
    for name in _SYMPY_NAMES:
        obj = getattr(sympy, name, None)
        if obj is not None:
            ns[name] = obj
    return ns


_SYMPY_NS = {}  # Filled only after the child installs OS limits.


class TemplateError(ValueError):
    """solution_template / answer_expr 不合法或不可求解。

    继承 ValueError 而不是抛裸异常：调用方（solve_isomorph）要把它转成
    ``computed=False`` + 可读 error，绝不能让它冒泡成 500。
    """


def _guard_tokens(src: str) -> None:
    """token 白名单 + 关键字黑名单；不合法就抛 TemplateError。"""
    if len(src) > _MAX_TEMPLATE_CHARS:
        raise TemplateError(f"表达式超过 {_MAX_TEMPLATE_CHARS} 字符上限")
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        raise TemplateError(f"表达式无法解析：{exc}") from exc
    count = 0
    for tok in tokens:
        if tok.type in (
            tokenize.ENDMARKER, tokenize.NEWLINE, tokenize.NL,
            tokenize.INDENT, tokenize.DEDENT, tokenize.COMMENT,
        ):
            continue
        count += 1
        if count > _MAX_TOKENS:
            raise TemplateError(f"表达式超过 {_MAX_TOKENS} 个 token 上限")
        if tok.type == tokenize.OP:
            if tok.string not in _ALLOWED_OPS:
                raise TemplateError(f"不允许的运算符：{tok.string!r}")
        elif tok.type == tokenize.NAME:
            # 关键字必须是 NAME 才挡得住：lambda / if / import 在 token 类型上与普通
            # 名字无异，只有按字面比对关键字表才能识别。
            if keyword.iskeyword(tok.string) or tok.string in ("True", "False", "None"):
                raise TemplateError(f"不允许的关键字：{tok.string!r}")
        elif tok.type != tokenize.NUMBER:
            # 字符串字面量（tokenize.STRING）与 f-string 等一律拒绝
            raise TemplateError(f"不允许的语法元素：{tokenize.tok_name[tok.type]}")


def _const_magnitude(node) -> float:
    """AST 数值常量的量级；算不出精确值时返回 inf 表示「巨大」。

    **绝不能在这里直接算 ``b ** e``**：那正是要防的挂死路径。所以指数一旦超过上限就
    立刻返回 inf，把判定交给调用方，而不是先把大整数造出来。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return abs(float(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _const_magnitude(node.operand)
    if isinstance(node, ast.BinOp):
        left, right = _const_magnitude(node.left), _const_magnitude(node.right)
        if isinstance(node.op, ast.Pow):
            if left <= 1.0 or right != right or right == float("inf"):
                return left if right == 0 else float("inf")
            if right > _MAX_INT_EXPONENT:
                return float("inf")
            try:
                return abs(left ** right)
            except OverflowError:
                return float("inf")
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Add):
            return left + right
    return float("nan")   # 含符号：不是常量，规模由 sympy 自己管


def _guard_pow(node, depth: int = 0) -> None:
    """静态拒绝会导致 sympy 挂死的大整数幂运算。

    **必须先判定 node 自身、再递归子节点**：``9**9**9`` 的根节点就是那个致命的 Pow，
    只遍历 ``iter_child_nodes`` 会把它整个跳过（实测确实会跳，然后 sympy 永久不返回）。
    """
    if depth > 40:
        raise TemplateError("表达式嵌套过深")
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
        base = _const_magnitude(node.left)
        exponent = _const_magnitude(node.right)
        # 判定必须精确到「两端都是字面量」这一种形态，其余一律放行：
        #
        # - 指数为 nan = 含符号（``9**n``、``(1+p)**n``）：sympy 惰性保留、不会构造大整数，
        #   **必须放行**，否则合法的参数化模板会被误拒。
        # - 底数为 nan = 含符号（``p**9``）：同样不会构造大整数，放行。
        # - 只有「底数是大于 1 的字面量 + 指数是字面量且过大」才会触发大整数构造。
        #   实测 ``9**9**9`` 正是这一形态，会让 sympy 永久不返回（不是变慢）。
        base_is_literal = (base == base) and base != float("inf")
        exp_is_literal = (exponent == exponent) and exponent != float("inf")
        if base_is_literal and base > 1.0 and exponent == exponent and exponent > _MAX_INT_EXPONENT:
            raise TemplateError("整数幂规模超过上限（底数大于 1 且指数过大），拒绝求解")
    for child in ast.iter_child_nodes(node):
        _guard_pow(child, depth + 1)


def guard_source(src: str, *, what: str = "表达式") -> ast.Expression:
    text = str(src or "").strip()
    if not text:
        raise TemplateError(f"{what}为空")
    _guard_tokens(text)
    try:
        tree = ast.parse(text, mode="eval")
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise TemplateError(f"{what}语法错误") from exc
    if sum(1 for _ in ast.walk(tree)) > _MAX_AST_NODES:
        raise TemplateError(f"{what}超过 {_MAX_AST_NODES} 个语法节点上限")
    _guard_pow(tree.body)
    allowed = (ast.Expression, ast.Constant, ast.Name, ast.Load, ast.BinOp, ast.UnaryOp,
               ast.Call, ast.Tuple, ast.List, ast.Add, ast.Sub, ast.Mult, ast.Div,
               ast.Pow, ast.Mod, ast.FloorDiv, ast.UAdd, ast.USub)
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise TemplateError("不允许的表达式结构")
        if isinstance(node, ast.Name) and (node.id.startswith("_") or len(node.id) > 48):
            raise TemplateError("不允许的符号名")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _SYMPY_NAMES or node.keywords:
                raise TemplateError("不允许的数学函数")
            if len(node.args) > 8:
                raise TemplateError("数学函数参数数超过上限")
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
                raise TemplateError("只允许有限数字字面量")
            if not math.isfinite(node.value) or abs(node.value) > 10**12:
                raise TemplateError("数字量级超过上限")
    return tree


def _evaluate_ast(node):
    if isinstance(node, ast.Constant):
        return sympy.Integer(node.value) if isinstance(node.value, int) else sympy.Float(node.value)
    if isinstance(node, ast.Name):
        return _SYMPY_NS.get(node.id, sympy.Symbol(node.id))
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(_evaluate_ast(x) for x in node.elts)
    if isinstance(node, ast.UnaryOp):
        val = _evaluate_ast(node.operand)
        return -val if isinstance(node.op, ast.USub) else val
    if isinstance(node, ast.BinOp):
        left, right = _evaluate_ast(node.left), _evaluate_ast(node.right)
        if isinstance(node.op, ast.Pow):
            _guard_numeric_power(left, right)
        ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
               ast.Div: operator.truediv, ast.Pow: operator.pow,
               ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv}
        return ops[type(node.op)](left, right)
    if isinstance(node, ast.Call):
        args = [_evaluate_ast(x) for x in node.args]
        _guard_call(node.func.id, args)
        return _SYMPY_NS[node.func.id](*args)
    raise TemplateError("不允许的表达式结构")


def _guard_numeric_power(base, exponent):
    if exponent.is_number and exponent.is_real and abs(exponent) > _MAX_INT_EXPONENT:
        if base.is_number and abs(base) > 1:
            raise TemplateError("整数幂规模超过上限，拒绝求解")


def _guard_call(name, args):
    if name in ("factorial", "binomial", "Integer", "Float", "Rational", "Pow"):
        for val in args:
            if getattr(val, "is_number", False) and getattr(val, "is_real", False) and abs(val) > 500:
                raise TemplateError("数学函数计算规模超过上限")
    if name == "Pow" and len(args) >= 2:
        _guard_numeric_power(args[0], args[1])
    if name in ("Sum", "Product", "Integral", "limit", "diff"):
        for val in args[1:]:
            if isinstance(val, tuple) and len(val) >= 3:
                lo, hi = val[-2:]
                if getattr(lo, "is_number", False) and getattr(hi, "is_number", False) and abs(hi-lo) > 1000:
                    raise TemplateError("迭代范围超过计算规模上限")
    if name == "Matrix":
        if args and isinstance(args[0], tuple):
            if sum(len(x) if isinstance(x, tuple) else 1 for x in args[0]) > 64:
                raise TemplateError("矩阵规模超过上限")
        elif len(args) >= 2 and args[0] * args[1] > 64:
            raise TemplateError("矩阵规模超过上限")


def _guard_expression(expr):
    if not isinstance(expr, sympy.Basic):
        raise TemplateError("不是合法的数学表达式")
    if sympy.count_ops(expr) > _MAX_COUNT_OPS:
        raise TemplateError("表达式计算规模超过上限")
    for node in sympy.preorder_traversal(expr):
        if isinstance(node, sympy.Integer) and int(node).bit_length() > 4096:
            raise TemplateError("整数结果长度超过上限")
        if isinstance(node, sympy.Pow):
            _guard_numeric_power(*node.args)
        if node.func.__name__ in ("factorial", "binomial"):
            _guard_call(node.func.__name__, node.args)


def parse_sympy(src: str, *, what: str = "表达式"):
    tree = guard_source(src, what=what)
    try:
        expr = _evaluate_ast(tree.body)
        _guard_expression(expr)
        return expr
    except TemplateError:
        raise
    except Exception as exc:
        raise TemplateError(f"{what}无法解析：{exc.__class__.__name__}") from exc

# 未知量的保留符号名。solution_template 里出现它，表示「要求解的就是这个量」；
# 代入 incidentals 时**绝不**替换它。
UNKNOWN_SYMBOL = "ANS"

# 多解时的选取规则白名单。必须显式声明才选，绝不猜：
# 「同一道题给出多个互斥答案」正是本模块要根治的病（E018），求解器自己再犯一次
# 就毫无意义了。未声明规则又确实多解时，返回 computed=False 并说明原因。
ANSWER_SELECTORS = ("positive", "negative", "max", "min", "nonnegative")


def _to_sympy_value(value):
    """把一个 incidental 值转成 sympy 对象；转不了返回 None（调用方判失败，不猜）。

    接受 int / float / Decimal 字面量、``"3/4"`` 这类分数字符串，以及 ``"sqrt(2)"``
    这类需要走同一套守卫的表达式字符串。布尔值**不接受**：``True`` 在 Python 里等于
    ``1``，静默当成数字会让「参数填错了」变成「答案算错了」，两者都难排查。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return parse_sympy(str(value), what="参数值")
    if isinstance(value, float):
        return parse_sympy(str(value), what="参数值")
    if isinstance(value, sympy.Basic):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        guard_source(text, what="参数值")
        # 纯小数 / 分数优先按精确有理数解析，避免 0.1 变成二进制浮点误差
        m = re.fullmatch(r"([+-]?\d+)\s*/\s*(\d+)", text)
        if m:
            denom = int(m.group(2))
            return sympy.Rational(int(m.group(1)), denom) if denom else None
        if re.fullmatch(r"[+-]?\d+", text):
            return sympy.Integer(int(text))
        if re.fullmatch(r"[+-]?\d*\.\d+([eE][+-]?\d+)?", text):
            return sympy.Rational(text)
        try:
            return parse_sympy(text, what="参数值")
        except TemplateError:
            return None
    return None


def _subs_values(expr, values: dict):
    """代入参数；返回 (结果表达式, 未能解析的键列表)。"""
    mapping: dict = {}
    unresolved: list[str] = []
    for key, raw in (values or {}).items():
        name = str(key)
        val = _to_sympy_value(raw)
        if val is None:
            unresolved.append(name)
            continue
        mapping[sympy.Symbol(name)] = val
    if not mapping:
        return expr, unresolved
    for node in sympy.preorder_traversal(expr):
        if isinstance(node, sympy.Pow):
            _guard_numeric_power(node.base.xreplace(mapping), node.exp.xreplace(mapping))
    try:
        # mapping 的键恒为 sympy.Symbol（见上），所以用 xreplace：它做纯结构替换，
        # 不会像 subs 那样触发额外化简与求值——更快，也不会引入意外的重写。
        out = expr.xreplace(mapping)
    except Exception as exc:  # noqa: BLE001
        raise TemplateError(f"代入参数失败：{exc.__class__.__name__}: {str(exc)[:120]}") from exc
    _guard_expression(out)
    # xreplace 不做算术化简，这里补一次；失败就保留原样（不因为化简失败而判无解）
    try:
        out = sympy.simplify(out) if sympy.count_ops(out) <= 60 else out
    except Exception:  # noqa: BLE001
        pass
    return out, unresolved


def _pick_solution(solutions: list, selector: str):
    """从多个候选解里按**显式声明**的规则挑一个；挑不出就返回 (None, 原因)。"""
    if not solutions:
        return None, "方程无解"
    if len(solutions) == 1:
        return solutions[0], ""
    if selector == "positive":
        pool = [s for s in solutions if (s.is_positive is True)]
    elif selector == "nonnegative":
        pool = [s for s in solutions if (s.is_positive is True or s.is_zero is True)]
    elif selector == "negative":
        pool = [s for s in solutions if (s.is_negative is True)]
    elif selector == "max":
        pool = []
        try:
            pool = [max(solutions, key=lambda s: complex(sympy.N(s)).real)]
        except Exception:  # noqa: BLE001
            pool = []
    elif selector == "min":
        pool = []
        try:
            pool = [min(solutions, key=lambda s: complex(sympy.N(s)).real)]
        except Exception:  # noqa: BLE001
            pool = []
    else:
        pool = []
    if len(pool) == 1:
        return pool[0], ""
    shown = ", ".join(str(s) for s in solutions[:4])
    if not selector:
        return None, (
            f"方程有 {len(solutions)} 个解（{shown}），但母题未声明 answer_select 选取规则；"
            "拒绝任选其一（那正是「同题多解」的病根）"
        )
    return None, f"answer_select={selector!r} 未能唯一确定解（候选：{shown}）"


def _is_unusable(expr) -> str:
    r"""结果是不是「数学上无意义」的；是则返回原因，否则返回空串。

    必须挡掉 ``zoo`` 与 ``nan``：实测 ``1/(p-2)`` 在 ``p=2`` 时 sympy 给出 ``zoo``
    （复无穷），若原样当成答案交付，学生看到的就是一个除零错误的产物。``nan``
    同理（``0/0``、``oo-oo``）。

    **``oo`` / ``-oo`` 要放行**：它们是极限题的合法答案（实测那份卷的选项里就有
    ``D. $+\infty$``），不是错误。
    """
    try:
        if expr.has(sympy.zoo, sympy.nan):
            return "求解结果为未定义值（除零或不定式），拒绝当作答案"
        if expr is sympy.zoo or expr is sympy.nan:
            return "求解结果为未定义值（除零或不定式），拒绝当作答案"
    except Exception:  # noqa: BLE001  has() 自身异常时不拦，交由后续判定
        return ""
    return ""


def _format_answer(expr) -> dict:
    """把 sympy 结果格式化成可交付文本。

    ``answer`` 用 ``str(expr)``：确定、可复现、可直接比对（``sqrt(2)``、``1/2``、
    ``exp(-1)``）。``answer_latex`` 供 Markdown 数学环境使用。``numeric`` 供快速核对，
    算不出（含符号 / 无界）就是空串，不硬凑。
    """
    _guard_expression(expr)
    try:
        latex = sympy.latex(expr)
    except Exception:  # noqa: BLE001
        latex = ""
    numeric = ""
    try:
        if not expr.free_symbols:
            val = sympy.N(expr, 10)
            if val.is_finite is not False and not val.has(sympy.zoo, sympy.nan, sympy.oo):
                numeric = str(val)
    except Exception:  # noqa: BLE001
        numeric = ""
    return {"answer": str(expr), "answer_latex": latex, "numeric": numeric}


def solve_isomorph(item_model_dict: dict, incidental_values: dict) -> dict:
    """把 incidentals 代入母题的参数化解法，用 sympy **机械求解**出答案。

    返回 ``{"answer": str, "computed": bool, "error": str}``（另附 answer_latex /
    numeric 供渲染与核对）。``computed=False`` 时 ``answer`` 恒为空串——**求解失败绝不
    编造答案**，这是本模块最重要的一条约束：一个看起来很确定的错答案会被直接交付给
    学生，危害远大于「这题算不出来」。

    两种求解形态（按优先级）：

    1. ``answer_expr`` 非空：它就是答案的参数化表达式（如 ``sqrt(p)/2``），代入后直接
       化简。适合「答案 = 参数的闭式函数」的绝大多数客观题。
    2. ``solution_template`` 非空：
       - 是关系式（``Eq(ANS**2 - p, 0)``）→ 对未知量求解，按 ``answer_select`` 选解；
       - 是普通表达式 → 视作答案表达式本身（与形态 1 等价）。

    两者都为空 → ``computed=False``。抽出的母题默认没有模板（不能凭空造），必须由人
    补录或从已确认的典型题导入；在那之前该母题**不产出** isomorph 变式。
    """
    result = {"answer": "", "answer_latex": "", "numeric": "", "computed": False, "error": ""}
    model = item_model_dict if isinstance(item_model_dict, dict) else {}
    unknown_name = str(model.get("unknown") or UNKNOWN_SYMBOL).strip() or UNKNOWN_SYMBOL
    unknown = sympy.Symbol(unknown_name)
    selector = str(model.get("answer_select") or "").strip().lower()
    if selector and selector not in ANSWER_SELECTORS:
        result["error"] = f"未知的 answer_select 规则：{selector!r}（可选：{'/'.join(ANSWER_SELECTORS)}）"
        return result

    values = dict(incidental_values or {})
    # 未知量不能被当作参数代入，否则方程会被消掉
    values.pop(unknown_name, None)

    declared = model.get("incidentals") or {}
    if isinstance(declared, dict) and declared:
        missing = sorted(k for k in declared if k not in values and k != unknown_name)
        if missing:
            result["error"] = f"缺少母题声明的参数：{', '.join(missing)}"
            return result

    expr_src = str(model.get("answer_expr") or "").strip()
    tmpl_src = str(model.get("solution_template") or "").strip()
    if not expr_src and not tmpl_src:
        result["error"] = "母题未定义 answer_expr / solution_template，无法机械求解（拒绝编造答案）"
        return result

    try:
        if expr_src:
            expr = parse_sympy(expr_src, what="answer_expr")
            solved, unresolved = _subs_values(expr, values)
            if unresolved:
                result["error"] = f"参数值无法解析为数学量：{', '.join(unresolved)}"
                return result
            if solved.free_symbols:
                result["error"] = (
                    "代入后仍含未定符号："
                    + ", ".join(sorted(str(s) for s in solved.free_symbols))
                )
                return result
            bad = _is_unusable(solved)
            if bad:
                result["error"] = bad
                return result
            result.update(_format_answer(solved))
            result["computed"] = True
            return result

        tmpl = parse_sympy(tmpl_src, what="solution_template")
        substituted, unresolved = _subs_values(tmpl, values)
        if unresolved:
            result["error"] = f"参数值无法解析为数学量：{', '.join(unresolved)}"
            return result
        if substituted.free_symbols and not (substituted.free_symbols == {unknown}):
            result["error"] = (
                "代入后仍含未定符号："
                + ", ".join(sorted(str(s) for s in (substituted.free_symbols - {unknown})))
            )
            return result
        if isinstance(substituted, sympy.logic.boolalg.Boolean) or substituted.has(sympy.Eq):
            if not substituted.has(unknown):
                result["error"] = f"解法模板不含未知量 {unknown_name}，无法求解"
                return result
            try:
                solutions = sympy.solve(substituted, unknown)
            except Exception as exc:  # noqa: BLE001  解不出如实报告
                result["error"] = f"符号求解失败：{exc.__class__.__name__}: {str(exc)[:120]}"
                return result
            picked, reason = _pick_solution(list(solutions or []), selector)
            if picked is None:
                result["error"] = reason
                return result
            bad = _is_unusable(picked)
            if bad:
                result["error"] = bad
                return result
            result.update(_format_answer(picked))
            result["computed"] = True
            return result
        # 普通表达式：化简后即为答案
        if substituted.free_symbols:
            result["error"] = (
                "解法模板化简后仍含未定符号："
                + ", ".join(sorted(str(s) for s in substituted.free_symbols))
            )
            return result
        bad = _is_unusable(substituted)
        if bad:
            result["error"] = bad
            return result
        result.update(_format_answer(substituted))
        result["computed"] = True
        return result
    except TemplateError as exc:
        result["error"] = str(exc)
        return result
    except Exception as exc:  # noqa: BLE001  兜底：任何异常都转成「算不出」，不冒泡成 500
        logger.warning("机械求解异常：%s", exc)
        result["error"] = f"求解异常：{exc.__class__.__name__}: {str(exc)[:120]}"
        return result



# Keep the Windows handle alive until process exit: closing it kills the job.
_JOB_HANDLE = None


def _apply_process_limits(memory_bytes: int):
    global _JOB_HANDLE
    if os.name != "nt":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
        return "rlimit_as"
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                     "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise OSError(ctypes.get_last_error(), "Cannot create symbolic memory job")
    limits = Extended()
    # PROCESS_MEMORY | JOB_MEMORY | ACTIVE_PROCESS | KILL_ON_JOB_CLOSE
    limits.BasicLimitInformation.LimitFlags = 0x100 | 0x200 | 0x8 | 0x2000
    limits.BasicLimitInformation.ActiveProcessLimit = 1
    limits.ProcessMemoryLimit = limits.JobMemoryLimit = memory_bytes
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = ctypes.get_last_error()
        kernel.CloseHandle(handle)
        raise OSError(error, "Cannot set symbolic memory limit")
    if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        error = ctypes.get_last_error()
        kernel.CloseHandle(handle)
        raise OSError(error, "Cannot assign symbolic memory job")
    _JOB_HANDLE = handle
    return "windows_job_commit"


def main():
    global sympy, _SYMPY_NS
    try:
        limiter = _apply_process_limits(int(sys.argv[1]))
        raw = sys.stdin.buffer.read(32769)
        if len(raw) > 32768:
            raise TemplateError("求解请求长度超过上限")
        request = json.loads(raw)
        import sympy
        _SYMPY_NS = _sympy_namespace()
        operation = request.get("operation")
        if operation == "validate":
            expr = parse_sympy(request.get("text"), what=request.get("what", "表达式"))
            data = {"expression": str(expr)}
        elif operation == "defaults":
            values = {}
            for key, raw in request.get("values", {}).items():
                try:
                    val = _to_sympy_value(raw)
                    if val is not None and val.is_number is True and not _is_unusable(val):
                        values[key] = raw
                except TemplateError:
                    continue
            data = {"values": values}
        elif operation == "solve":
            data = solve_isomorph(request.get("model", {}), request.get("values", {}))
        else:
            raise TemplateError("未知符号操作")
        reply = {"ok": True, "data": data, "memory_limiter": limiter}
    except MemoryError:
        reply = {"ok": False, "error": "符号计算超过内存上限"}
    except Exception as exc:
        reply = {"ok": False, "error": str(exc)[:200] or "符号执行失败"}
    try:
        output = json.dumps(reply, ensure_ascii=True).encode("ascii")
        if len(output) > 32768:
            output = b'{"ok":false,"error":"Symbolic output exceeds size limit"}'
        sys.stdout.buffer.write(output)
        sys.stdout.buffer.flush()
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
