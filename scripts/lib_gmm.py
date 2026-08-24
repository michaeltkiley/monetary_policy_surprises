"""
Shared GMM/IV estimation helpers for the Kiley (2014 JMCB) and Kiley (2016
FRL) replications: 2-step IV-GMM point estimates via linearmodels, plus the
diagnostic battery both papers report per specification -- J-test, weak
instrument (Cragg-Donald F / Stock-Yogo critical value), and the
Andrews-Fair (1988) two-sample parameter stability test.

References:
  Hansen, L. (1982), "Large Sample Properties of Generalized Method of
    Moments Estimators," Econometrica 50(4).
  Stock, J.H. and M. Yogo (2002), "Testing for Weak Instruments in Linear
    IV Regression," NBER TWP 0284.
  Andrews, D.W.K. and R.C. Fair (1988), "Inference in Nonlinear Econometric
    Models with Structural Change," Review of Economic Studies 55.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import statsmodels.api as sm
from linearmodels.iv import IVGMM

# Stock-Yogo (2002) 10% maximal-IV-size critical values for the Cragg-Donald
# F-statistic, single endogenous regressor, by number of instruments.
STOCK_YOGO_10PCT = {1: 16.38, 2: 19.93, 3: 22.30, 4: 24.58, 5: 26.87}


@dataclass
class FitResult:
    label: str
    n_obs: int
    params: pd.Series
    se: pd.Series
    cov: pd.DataFrame
    j_stat: float = None
    j_pvalue: float = None
    overid_df: int = None
    cragg_donald_f: float = None
    stock_yogo_cv: float = None

    def coef_str(self, name):
        return f"{self.params[name]:.2f} ({self.se[name]:.2f})"


def ols_hc(df: pd.DataFrame, dep: str, regressors: list[str], label: str = "OLS") -> FitResult:
    """OLS with HC1 heteroskedasticity-robust standard errors (matches the
    papers' 'standard errors corrected for heteroskedasticity')."""
    X = sm.add_constant(df[regressors])
    y = df[dep]
    res = sm.OLS(y, X).fit(cov_type="HC1")
    return FitResult(
        label=label, n_obs=int(res.nobs),
        params=res.params, se=res.bse, cov=res.cov_params(),
    )


def iv_gmm(df: pd.DataFrame, dep: str, endog: list[str], instruments: list[str],
           exog: list[str] = None, label: str = "IV-GMM") -> FitResult:
    """2-step IV-GMM (Hansen 1982), heteroskedasticity-robust weighting --
    matches both papers' estimation method. Reports the J-test of
    overidentifying restrictions when the model is overidentified."""
    exog = exog or []
    y = df[dep]
    X_exog = sm.add_constant(df[exog]) if exog else pd.DataFrame({"const": np.ones(len(df))}, index=df.index)
    X_endog = df[endog]
    Z = df[instruments]
    mod = IVGMM(y, X_exog, X_endog, Z)
    res = mod.fit(cov_type="robust", iter_limit=2)

    n_instr = X_exog.shape[1] + Z.shape[1]
    n_params = X_exog.shape[1] + X_endog.shape[1]
    overid_df = n_instr - n_params

    j_stat = j_pvalue = None
    reported_overid_df = None
    if overid_df > 0:
        j_stat = float(res.j_stat.stat)
        j_pvalue = float(res.j_stat.pval)
        reported_overid_df = overid_df

    cd_f = sy_cv = None
    if len(endog) == 1:
        cd_f = cragg_donald_fstat(df, endog[0], instruments, exog)
        sy_cv = STOCK_YOGO_10PCT.get(len(instruments))
    else:
        cd_f = cragg_donald_multi(df, endog, instruments, exog)

    return FitResult(
        label=label, n_obs=int(res.nobs),
        params=res.params, se=res.std_errors, cov=res.cov,
        j_stat=j_stat, j_pvalue=j_pvalue, overid_df=reported_overid_df,
        cragg_donald_f=cd_f, stock_yogo_cv=sy_cv,
    )


def cragg_donald_fstat(df: pd.DataFrame, endog: str, instruments: list[str],
                        exog: list[str] = None) -> float:
    """Cragg-Donald (1993) weak-instrument F-statistic for a single
    endogenous regressor: the classical first-stage F-stat on the excluded
    instruments, from regressing the endogenous regressor on all exogenous
    regressors (including instruments) with a constant."""
    exog = exog or []
    X_full = sm.add_constant(df[exog + instruments])
    y = df[endog]
    res_full = sm.OLS(y, X_full).fit()
    X_restricted = sm.add_constant(df[exog]) if exog else pd.DataFrame(
        {"const": np.ones(len(df))}, index=df.index)
    res_restricted = sm.OLS(y, X_restricted).fit()

    n = res_full.nobs
    k_full = X_full.shape[1]
    q = len(instruments)
    rss_r, rss_u = res_restricted.ssr, res_full.ssr
    f_stat = ((rss_r - rss_u) / q) / (rss_u / (n - k_full))
    return float(f_stat)


def cragg_donald_multi(df: pd.DataFrame, endog: list[str], instruments: list[str],
                        exog: list[str] = None) -> float:
    """Cragg-Donald (1993) weak-instrument statistic for multiple endogenous
    regressors: n * (minimum eigenvalue of the canonical-correlation matrix
    between the endogenous regressors and the excluded instruments, both
    residualized on the included exogenous regressors), divided by the
    number of excluded instruments L (Stock-Yogo 2005 normalization). This
    is the multivariate generalization of the classical first-stage F-stat
    used for a single endogenous regressor -- with k>1 endogenous variables,
    a strong *marginal* first-stage fit for each regressor individually does
    not guarantee the two are jointly well identified; the minimum canonical
    correlation is the relevant diagnostic."""
    from scipy.linalg import sqrtm

    exog = exog or []
    cols_exog = ["__const__"] + exog
    X_exog = df[exog].to_numpy() if exog else np.empty((len(df), 0))

    def residualize(M):
        if X_exog.shape[1] == 0:
            return M - M.mean(axis=0)
        Xc = sm.add_constant(X_exog)
        beta, *_ = np.linalg.lstsq(Xc, M, rcond=None)
        return M - Xc @ beta

    V = residualize(df[endog].to_numpy())
    Z = residualize(df[instruments].to_numpy())
    n = len(df)
    Svv = V.T @ V / n
    Szz = Z.T @ Z / n
    Svz = V.T @ Z / n
    Svv_isqrt = np.linalg.inv(sqrtm(Svv))
    canon = Svv_isqrt @ Svz @ np.linalg.inv(Szz) @ Svz.T @ Svv_isqrt.T
    eigvals = np.linalg.eigvalsh(canon.real) * n
    L = len(instruments)
    return float(eigvals.min() / L)


def andrews_fair_test(fit1: FitResult, fit2: FitResult, coef_names: list[str]) -> tuple[float, float, int]:
    """Andrews-Fair (1988) Wald test for parameter stability across two
    disjoint (independently estimated) samples: for the coefficients in
    coef_names, test H0: theta_1 = theta_2 using
        W = (theta_1 - theta_2)' [V_1 + V_2]^-1 (theta_1 - theta_2)  ~ chi2(k)
    which follows because the two subsample GMM estimators are independent,
    so Cov(theta_1 - theta_2) = V_1 + V_2. Returns (statistic, p-value, df).
    """
    from scipy import stats as sstats

    d = np.array([fit1.params[c] - fit2.params[c] for c in coef_names])
    V = (fit1.cov.loc[coef_names, coef_names].to_numpy()
         + fit2.cov.loc[coef_names, coef_names].to_numpy())
    stat = float(d @ np.linalg.solve(V, d))
    dof = len(coef_names)
    pval = float(1 - sstats.chi2.cdf(stat, dof))
    return stat, pval, dof


def hall_sen_test(fit1: FitResult, fit2: FitResult) -> tuple[float, float, int]:
    """Hall-Sen (1999) test for stability of the overidentifying restrictions
    across two subsamples, known breakpoint (Hall & Sen, JBES 17(3), Section
    2, eq. 2.6-2.8 and Theorem 2.1). Unlike Andrews-Fair (which tests
    parameter constancy), this tests whether the *extra* moment conditions
    -- instrument validity beyond just-identification -- hold separately in
    each regime, allowing coefficients to differ.

    O_T(pi) = O_{1,T}(pi) + O_{2,T}(pi), where O_{i,T}(pi) is simply the
    ordinary Hansen (1982) overidentifying-restrictions (J) statistic
    computed *within* subsample i, using that subsample's own two-step GMM
    estimate and weighting matrix -- i.e., each fit's own j_stat, exactly as
    already computed by iv_gmm(). Hall & Sen show O_{1,T} and O_{2,T} are
    each asymptotically chi2(q-p) under the null and asymptotically
    independent (disjoint subsamples), so their sum is chi2(2(q-p))
    (Theorem 2.1: "O_T(pi) converges to a noncentral chi2_{2q-2p}" under
    local alternatives, chi2_{2q-2p} under the null). Requires both fits to
    be overidentified (fit.j_stat is not None).
    """
    from scipy import stats as sstats

    if fit1.j_stat is None or fit2.j_stat is None:
        raise ValueError("Hall-Sen test requires both fits to be overidentified (has a J-stat)")
    stat = fit1.j_stat + fit2.j_stat
    dof = fit1.overid_df + fit2.overid_df
    pval = float(1 - sstats.chi2.cdf(stat, dof))
    return stat, pval, dof


def print_fit(fit: FitResult, coef_names: list[str] = None):
    print(f"[{fit.label}] n={fit.n_obs}")
    names = coef_names or list(fit.params.index)
    for name in names:
        print(f"  {name}: {fit.coef_str(name)}")
    if fit.j_stat is not None:
        print(f"  J-test: stat={fit.j_stat:.2f}, p={fit.j_pvalue:.2f}")
    if fit.cragg_donald_f is not None:
        print(f"  Cragg-Donald F={fit.cragg_donald_f:.1f} (Stock-Yogo 10% CV={fit.stock_yogo_cv})")
