import numpy as np
import cupy as cp

from math import sqrt, exp, log
sqrt2 = 1/sqrt(2)


from cupyx.scipy.special import ndtr as ndtr

def Phi(x):
    return 0.5 + 0.5 * cupy_erf(x * sqrt2)

from scipy.stats import norm



from typing import Union, Callable, Optional
from copy import error
from scipy.interpolate import RectBivariateSpline
from scipy.optimize import newton, root_scalar

from numba import jit, njit, prange, float64
from numba.experimental import jitclass

if __name__ == '__main__':
    print("This is a module. Please import it.\n")
    exit(-1)

def european_call_payoff_cupy(maturity: float,
                         strike: float,
                         interest_rate: float = 0.):
    @cp.fuse
    def payoff(St: np.ndarray):
        DF = cp.exp( - interest_rate * maturity)
        return cp.maximum(St - strike, 0.)*DF

    return payoff

@jitclass([("kappa", float64),
           ("gamma", float64),
           ("rho", float64), 
           ("vbar", float64),
           ("v0", float64)])
class HestonParameters:
    def __init__(self, kappa, gamma, rho, vbar, v0):
        self.kappa = kappa
        self.gamma = gamma
        self.rho = rho
        self.vbar = vbar
        self.v0 = v0
        
@jitclass([("stock_price", float64),
           ("interest_rate", float64)])
class MarketState:
    def __init__(self, stock_price, interest_rate):
        self.stock_price = stock_price
        self.interest_rate = interest_rate

def get_len_conf_interval(data:             np.ndarray, 
                          confidence_level: float = 0.05):
    """Get the confidence interval length for a given confidence level.
    Args:
        data:             The data to compute the confidence interval for.
        confidence_level: The confidence level to use.
    
    Returns:
        The confidence interval.
    """
    return -2*norm.ppf(confidence_level*0.5) * sqrt(np.var(data) / len(data))

def mc_price_cupy(payoff:                 Callable,
             simulate:               Callable,
             state:                  MarketState,
             heston_params:          HestonParameters,
             T:                      float    = 1.,
             N_T:                    int      = 100,
             absolute_error:         float    = 0.01,
             confidence_level:       float    = 0.05,
             batch_size:             int      = 10_000,
             MAX_ITER:               int      = 100_000,
             control_variate_payoff: Callable = None,
             control_variate_iter:   int      = 1_000,
             verbose:                bool     = False,
             random_seed:            int      = None,
             **kwargs):
    """A function that performs a Monte-Carlo based pricing of a derivative with a given payoff (possibly path-dependent) under the Heston model.
    Args:
        payoff (Callable):                           Payoff function
        simulate (Callable):                         Simulation engine
        state (MarketState):                         Market state
        heston_params (HestonParameters):            Heston parameters
        T (float, optional):                         Contract expiration T. Defaults to 1.. 
        N_T (int, optional):                         Number of steps in time. Defaults to 100.
        absolute_error (float, optional):            Absolute error of the price. Defaults to 0.01 (corresponds to 1 cent). 
        confidence_level (float, optional):          Confidence level for the price. Defaults to 0.05.
        batch_size (int, optional):                  Path-batch size. Defaults to 10_000.
        MAX_ITER (int, optional):                    Maximum number of iterations. Defaults to 100_000.  
        control_variate_payoff (Callable, optional): Control variate payoff. Defaults to None.
        control_variate_iter (int, optional):        Number of iterations for the control variate. Defaults to 1_000.
        verbose (bool, optional):                    Verbose flag. If true, the technical information is printed. Defaults to False.
        random_seed (int, optional):                 Random seed. Defaults to None.
        **kwargs:                                    Additional arguments for the simulation engine.
    Returns:    
        The price(-s) of the derivative(-s).    
    """

    arg = {'state':         state,
           'heston_params': heston_params, 
           'T':             T, 
           'N_T':           N_T, 
           'n_simulations': batch_size}

    args       = {**arg, **kwargs}
    iter_count = 0   

    length_conf_interval = 1.
    n                    = 0
    C                    = -2*norm.ppf(confidence_level*0.5)
    sigma_n              = 0.
    batch_new            = cp.zeros(batch_size, dtype=cp.float64)
    current_Pt_sum       = 0.        

    if random_seed is not None:
        cp.random.seed(random_seed)
    
    #@cp.fuse
    #def kernel2(batch_new, sigma_n, n, batch_size, current_Pt_sum):
    #    sigma_n = (sigma_n*(n-1.) + cp.var(batch_new)*(2*batch_size - 1.))/(n + 2*batch_size - 1.)
    #    current_Pt_sum = current_Pt_sum + cp.sum(batch_new) 
    #
    #    n+=2*batch_size
    #    length_conf_interval = C * cp.sqrt(sigma_n / n)
    #    return sigma_n, n, length_conf_interval, current_Pt_sum


    if control_variate_payoff is None:
        while length_conf_interval > absolute_error and iter_count < MAX_ITER:
            batch_new = payoff(simulate(**args)[0])

            #batch_new = batch_new[~cp.isnan(batch_new)]

            iter_count+=1

            sigma_n = (sigma_n*(n-1.) + cp.var(batch_new)*(4*batch_size - 1.))/(n + 4*batch_size - 1.)
            current_Pt_sum = current_Pt_sum + cp.sum(batch_new) 

            n+=4*batch_size
            length_conf_interval = C * cp.sqrt(sigma_n / n)
    else:
        S = simulate(control_variate_iter)
        c = cp.cov(payoff(S), control_variate_payoff(S))
        theta = c[0, 1] / c[1, 1]
        while length_conf_interval > absolute_error and iter_count < MAX_ITER:
            batch_new = payoff(simulate(**args)[0]) - theta * control_variate_payoff(simulate(**args)[0])
            iter_count+=1

            sigma_n = (sigma_n*(n-1.) + cp.var(batch_new)*(4*batch_size - 1.))/(n + 4*batch_size - 1.)
            current_Pt_sum = current_Pt_sum + cp.sum(batch_new) 

            n+=4*batch_size
            length_conf_interval = C * cp.sqrt(sigma_n / n)

    if verbose:
        if random_seed is not None:
            print(f"Random seed:                {random_seed}")

        if control_variate_payoff is not None:
            print(f"Control variate payoff:     {control_variate_payoff.__name__}")
            print(f"Control variate iterations: {control_variate_iter}")
        
        print(f"Number of simulate calls:   {iter_count}\nMAX_ITER:                   {MAX_ITER}\nNumber of paths:            {n}\nAbsolute error:             {absolute_error}\nLength of the conf intl:    {length_conf_interval}\nConfidence level:           {confidence_level}\n")

    return current_Pt_sum/n


@cp.fuse
def kernel_euler(s, v, z0, z1, kappa, vbar, gamma, rho, dt, r):
    vmax = cp.maximum(v, 0)
    sqrtvmaxdt = cp.sqrt(vmax*dt)
    s = s + (r - 0.5 * vmax) * dt + sqrtvmaxdt * z0
    v = v + kappa*(vbar - vmax)*dt + gamma*sqrtvmaxdt*(rho*z0+cp.sqrt(1-rho**2)*z1)
    return s, v


def simulate_heston_euler_cupy(state:      MarketState,
                          heston_params:   HestonParameters,
                          T:               float = 1.,
                          N_T:             int   = 100,
                          n_simulations:   int   = 10_000
                          ) -> np.ndarray:
    """Simulation engine for the Heston model using the Euler scheme.
    Args:
        state (MarketState):              Market state.
        heston_params (HestonParameters): Parameters of the Heston model.
        T (float, optional):              Contract termination time expressed as a number of years. Defaults to 1..
        N_T (int, optional):              Number of steps in time. Defaults to 100.
        n_simulations (int, optional):    Number of simulations. Defaults to 10_000.
    Raises:
        error: Contract termination time must be positive.
    Returns:
        dict: a tuple containing the simulated stock price and the simulated stochastic variance
    """    
    if T <= 0:
        raise error("Contract termination time must be positive.")
    
    # initialize market and model parameters
    r, s0 = state.interest_rate, state.stock_price
    
    v0, rho, kappa, vbar, gamma = heston_params.v0, heston_params.rho, heston_params.kappa, \
                                  heston_params.vbar, heston_params.gamma
    
    dt         = T/float(N_T)
    
    Z          = cp.random.standard_normal(size=(2 ,n_simulations, N_T), dtype=cp.float32)
    V_1          = cp.empty([n_simulations, N_T], dtype=cp.float32)
    V_1[:, 0]    = v0
    
    logS_1       = cp.empty([n_simulations, N_T], dtype=cp.float32)
    logS_1[:, 0] = cp.log(s0)

    V_2          = cp.empty([n_simulations, N_T], dtype=cp.float32)
    V_2[:, 0]    = v0
    
    logS_2       = cp.empty([n_simulations, N_T], dtype=cp.float32)
    logS_2[:, 0] = cp.log(s0)

    V_3          = cp.empty([n_simulations, N_T], dtype=cp.float32)
    V_3[:, 0]    = v0
    
    logS_3       = cp.empty([n_simulations, N_T], dtype=cp.float32)
    logS_3[:, 0] = cp.log(s0)

    V_4          = cp.empty([n_simulations, N_T], dtype=cp.float32)
    V_4[:, 0]    = v0
    
    logS_4       = cp.empty([n_simulations, N_T], dtype=cp.float32)
    logS_4[:, 0] = cp.log(s0)


    for i in range(0,  N_T-1):
        #vmax         = cp.maximum(V[:, i],0)

        #logS[:, i+1] = logS[:, i] + (r - 0.5 * vmax) * (dt) + cp.sqrt(vmax*(dt)) * Z[0, :, i]

        #V[:, i+1]    = V[:, i] + kappa*(vbar - vmax)*(dt) + gamma*cp.sqrt(vmax*(dt))*(rho*Z[0, :, i]+math.sqrt(1-rho**2)*Z[1, :, i])

        logS_1[:, i+1], V_1[:, i+1] = kernel_euler(logS_1[:, i], V_1[:, i], Z[0, :, i], Z[1, :, i], kappa, vbar, gamma, rho, dt, r)
        logS_2[:, i+1], V_2[:, i+1] = kernel_euler(logS_2[:, i], V_2[:, i], Z[0, :, i], -Z[1, :, i], kappa, vbar, gamma, rho, dt, r)
        logS_3[:, i+1], V_3[:, i+1] = kernel_euler(logS_3[:, i], V_3[:, i], -Z[0, :, i], Z[1, :, i], kappa, vbar, gamma, rho, dt, r)
        logS_4[:, i+1], V_4[:, i+1] = kernel_euler(logS_4[:, i], V_4[:, i], -Z[0, :, i], -Z[1, :, i], kappa, vbar, gamma, rho, dt, r)
        


    logS = cp.concatenate((logS_1, logS_2, logS_3, logS_4))
    V = cp.concatenate((V_1, V_2, V_3, V_3))
    return [cp.exp(logS[:, N_T-1]), V[:, N_T-1]]


def simulate_heston_andersen_qe_cupy(state:        MarketState,
                               heston_params: HestonParameters,
                               T:             float = 1.,
                               N_T:           int   = 100,
                               n_simulations: int   = 10_000,
                               Psi_c:         float = 1.5,
                               gamma_1:       float = 0.0     
                               ) -> dict: 
    """Simulation engine for the Heston model using the Quadratic-Exponential Andersen scheme.
    Args:
        state (MarketState):                 _description_
        heston_params (HestonParameters):    _description_
        T (float, optional):                 Contract termination time expressed as a non-integer amount of years. Defaults to 1..
        dt (float, optional): _description_. Defaults to 1e-2.
        n_simulations (int, optional):       _description_. Defaults to 10_000.
        Psi_c (float, optional):             _description_. Defaults to 1.5.
        gamma_1 (float, optional):           _description_. Defaults to 0.5.
    Raises:
        Error: The critical value \psi_c must be in the interval [1,2]
        Error: The parameter \gamma_1 must be in the interval [0,1]
    Returns:
        dict: a tuple containing the simulated stock price and the simulated stochastic variance
    """    
    
    if Psi_c>2 or Psi_c<1:
        raise error('The critical value \psi_c must be in the interval [1,2]')
    if gamma_1 >1 or gamma_1<0:
        raise error('The parameter \gamma_1 must be in the interval [0,1]')
    if T <= 0:
        raise error("Contract termination time must be positive.")
        
    gamma_2 = 1.0 - gamma_1
    
    r, s0 = state.interest_rate, state.stock_price
    v0, rho, kappa, vbar, gamma = heston_params.v0, heston_params.rho, heston_params.kappa, heston_params.vbar, heston_params.gamma
    
    dt         = T/float(N_T)
    E          = exp(-kappa*dt)
    K_0        = -(rho*kappa*vbar/gamma)*dt
    K_1        = gamma_1 * dt * (rho*kappa/gamma - 0.5) - rho/gamma
    K_2        = gamma_2 * dt * (rho*kappa/gamma - 0.5) + rho/gamma
    K_3        = gamma_1 * dt * (1.0 - rho**2)
    K_4        = gamma_2 * dt * (1.0 - rho**2)
        
    V          = cp.empty([2*n_simulations, N_T], dtype=cp.float64)
    V[:, 0]    = v0

    logS       = cp.empty([2*n_simulations, N_T], dtype=cp.float64)
    logS[:, 0] = cp.log(s0)

    Z          = cp.random.standard_normal(size=(2, n_simulations, N_T), dtype=cp.float64)

    p1 = (1. - E)*(gamma**2)*E/kappa
    p2 = (vbar*gamma**2)/(2.0*kappa)*((1-E)**2)
    p3 = vbar * (1.- E)
    rdtK0      = r*dt + K_0

    @cp.fuse
    def kernel1(v, E, p1, p2, p3):
        m            = p3 + v*E
        s_2          = v*p1 + p2
        Psi          = s_2/cp.power(m,2) 
    
        return m, Psi
    
    @cp.fuse
    def kernel2(s, v, v_1, z0, K_1, K_2, K_3, K_4, rdtK0):
        s = s + rdtK0 + K_1*v + K_2*v_1 - cp.sqrt(K_3*v+K_4*v_1) * z0
        return s
    
    for i in range(N_T - 1):

        m, Psi = kernel1(V[:, i], E, p1, p2, p3)
        filt = Psi<=Psi_c


        cond         = cp.where(filt)[0]
        #print(cond)
        #print(type(cond))
        c            = 2 / Psi[cond]
        b            = c - 1. + cp.sqrt(c*(c - 1.))
        a            = m[cond]/(1.+b)
        b            = cp.sqrt(b)
        V[cond, i+1] = a*(cp.power(b+Z[1, cond, i] , 2))

        cond         = cp.where(~filt)[0]
        p            = (Psi[cond] - 1)/(Psi[cond] + 1)
        beta         = (1.0 - p)/m[cond]
        U            = ndtr(Z[1,cond, i])

        V[cond,i+1] = cp.where(U < p, 0., cp.log((1-p)/(1-U))/beta)

# ------------------------------------------------- '++'
        
        logS[0:n_simulations ,i+1] = kernel2(logS[0: n_simulations,i], V[0: n_simulations,i], V[0:n_simulations,i+1], Z[0, :,i], K_1, K_2, K_3, K_4, rdtK0)
        #logS[0:n_simulations ,i+1] = logS[0: n_simulations,i] + rdtK0 + K_1*V[0: n_simulations,i] + K_2*V[0:n_simulations,i+1] \
        #                + cp.sqrt(K_3*V[0:n_simulations, i]+K_4*V[0:n_simulations, i+1]) * Z[0, :,i]
        

# ------------------------------------------------- '-+'

        logS[n_simulations : 2*n_simulations ,i+1] = kernel2(logS[n_simulations : 2*n_simulations,i], V[n_simulations : 2*n_simulations,i], V[n_simulations : 2*n_simulations,i+1], -Z[0, :,i], K_1, K_2, K_3, K_4, rdtK0)
        #logS[n_simulations : 2*n_simulations, i+1] = logS[n_simulations: 2*n_simulations,i] + rdtK0 + K_1*V[n_simulations: 2*n_simulations,i] + K_2*V[n_simulations: 2*n_simulations,i+1] \
        #                + cp.sqrt(K_3*V[n_simulations: 2*n_simulations,i]+K_4*V[n_simulations: 2*n_simulations,i+1]) * Z[0, :,i]
        
# ------------------------------------------------- '-+'
        
        #logS[2*n_simulations: 3*n_simulations ,i+1] = kernel2(logS[2*n_simulations: 3*n_simulations,i], V[2*n_simulations: 3*n_simulations,i], V[2*n_simulations: 3*n_simulations,i+1], -Z[0, :,i], K_1, K_2, K_3, K_4, rdtK0)
        #logS[2*n_simulations: 3*n_simulations,i+1] = logS[2*n_simulations: 3*n_simulations,i] + rdtK0 + K_1*V[2*n_simulations : 3*n_simulations,i] + K_2*V[2*n_simulations: 3*n_simulations,i+1] \
        #                - cp.sqrt(K_3*V[2*n_simulations: 3*n_simulations,i]+K_4*V[2*n_simulations: 3*n_simulations,i+1]) * Z[0, :,i]

# ------------------------------------------------- '--'


        #logS[3*n_simulations: 4*n_simulations,i+1] = logS[3*n_simulations: 4*n_simulations,i] + rdtK0 + K_1*V[3*n_simulations: 4*n_simulations,i] + K_2*V[3*n_simulations: 4*n_simulations,i+1] \
        #                - cp.sqrt(K_3*V[3*n_simulations: 4*n_simulations,i]+K_4*V[3*n_simulations: 4*n_simulations,i+1]) * Z[0, :,i]
        
           
    return [cp.exp(2*logS[:, N_T-1]), V[:, N_T-1]]




def calculate_r_for_andersen_tg(x_:      float,
                                maxiter: int = 2500, 
                                tol:     float = 1e-5
                                ):
    def foo(x: float):
        return x*norm.pdf(x) + norm.cdf(x)*(1+x**2) - (1+x_)*(norm.pdf(x) + x*norm.cdf(x))**2

    def foo_dif(x: float):
        return norm.pdf(x) - x**2 * norm.pdf(x) + norm.pdf(x)*(1+x**2) + 2*norm.cdf(x)*x - \
                2*(1+x_)*(norm.pdf(x) + x*norm.cdf(x))*(-norm.pdf(x)*x + norm.cdf(x) + x*norm.pdf(x) )

    def foo_dif2(x: float):
        return -x*norm.pdf(x) - 2*x* norm.pdf(x) + x**3 * norm.pdf(x) -x*norm.pdf(x)*(1+x**2) + \
                2*norm.cdf(x)*x + 2*norm.pdf(x)*x + 2*norm.cdf(x) + \
                2*(1+x_)*(-norm.pdf(x)*x + norm.cdf(x) + x*norm.pdf(x))**2 + \
                2*(1+x_)*(norm.pdf(x) + x*norm.cdf(x))*(x**2*norm.pdf(x) + norm.pdf(x) + norm.pdf(x) -x*norm.pdf(x) )

    return newton(foo,  x0 = 1/x_,fprime = foo_dif, fprime2 = foo_dif2, maxiter = maxiter , tol= tol )




@cp.fuse
def kernel_tg_1(v, E, p1, p2, p3, dx):
    m            = p3 + v*E
    s_2          = v*p1 + p2
    Psi          = s_2/cp.power(m,2) 
    
    return (Psi/dx).astype(int) , m, s_2

@cp.fuse
def kernel_tg_2(s, v, v_1, z0, z1, K_1, K_2, K_3, K_4, rdtK0, nu, sigma):
    v_1    = cp.maximum(nu + sigma*z1, 0)

    s = s + rdtK0 + K_1*v + K_2*v_1 - cp.sqrt(K_3*v+K_4*v_1) * z0
    return s, v_1

def simulate_heston_andersen_tg_cupy(state:         MarketState,
                                heston_params: HestonParameters,
                                x_grid:        cp.array,
                                f_nu_grid:     cp.array,
                                f_sigma_grid:  cp.array,
                                T:             float = 1.,
                                N_T:           int   = 100,
                                n_simulations: int   = 10_000,
                                gamma_1:       float = 0.0
                                ) -> dict: 
    """ Simulation engine for the Heston model using the Quadratic-Exponential Andersen scheme.
    Args:
        state (MarketState):              Market state.
        heston_params (HestonParameters): Parameters of the Heston model.
        x_grid (np.array):                _description_
        f_nu_grid (np.array):             _description_
        f_sigma_grid (np.array):          _description_
        T (float, optional):              Contract termination time expressed as a non-integer amount of years. Defaults to 1..
        dt (float, optional):             Time step. Defaults to 1e-2.
        n_simulations (int, optional):    number of the simulations. Defaults to 10_000.
        gamma_1 (float, optional):        _description_. Defaults to 0.0.
    Raises:
        error: The parameter \gamma_1 must be in the interval [0,1].
        error: Contract termination time must be positive.
    Returns:
        dict: a tuple containing the simulated stock price and the simulated stochastic variance.
    """    
    if gamma_1 >1 or gamma_1<0:
        raise error('The parameter \gamma_1 must be in the interval [0,1]')
    if T <= 0:
        raise error("Contract termination time must be positive.")
        
    gamma_2 = 1.0 - gamma_1
    
    r, s0 = state.interest_rate, state.stock_price
    v0, rho, kappa, vbar, gamma = heston_params.v0, heston_params.rho, heston_params.kappa, heston_params.vbar, heston_params.gamma
    
    dt         = T/float(N_T)
    E          = exp(-kappa*dt)
    K_0        = -(rho*kappa*vbar/gamma)*dt
    K_1        = gamma_1 * dt * (rho*kappa/gamma - 0.5) - rho/gamma
    K_2        = gamma_2 * dt * (rho*kappa/gamma - 0.5) + rho/gamma
    K_3        = gamma_1 * dt * (1.0 - rho**2)
    K_4        = gamma_2 * dt * (1.0 - rho**2)
        
    V          = cp.empty([4*n_simulations, N_T], dtype=cp.float32)
    V[:, 0]    = v0

    logS       = cp.empty([4*n_simulations, N_T], dtype=cp.float32)
    logS[:, 0] = cp.log(s0)

    Z          = cp.random.standard_normal(size=(2, n_simulations, N_T), dtype=cp.float32)
    p1 = (1. - E)*(gamma**2)*E/kappa
    p2 = (vbar*gamma**2)/(2.0*kappa)*((1-E)**2)
    p3 = vbar * (1.- E)
    rdtK0      = r*dt + K_0

    dx         = x_grid[1] - x_grid[0]


    
    
    for i in range(N_T - 1):

# ------------------------------------------------- '++'  
        inx, m, s_2 = kernel_tg_1(V[0:n_simulations, i], E, p1, p2, p3, dx)
        
        nu           = m * f_nu_grid[inx]
        sigma        = cp.sqrt(s_2)*f_sigma_grid[inx]

        logS[0:n_simulations,i+1], V[0:n_simulations, i+1] = kernel_tg_2(logS[0:n_simulations,i], V[0:n_simulations, i], V[0:n_simulations, i+1],
                                                Z[0,:,i], Z[1, :,i+1], K_1, K_2, K_3, K_4, rdtK0, nu, sigma)
        
# ------------------------------------------------- '+-'        
        inx, m, s_2 = kernel_tg_1(V[n_simulations:2*n_simulations, i], E, p1, p2, p3, dx)
        
        nu           = m * f_nu_grid[inx]
        sigma        = cp.sqrt(s_2)*f_sigma_grid[inx]

        logS[n_simulations:2*n_simulations,i+1], V[n_simulations:2*n_simulations, i+1] = kernel_tg_2(logS[n_simulations:2*n_simulations,i], V[n_simulations:2*n_simulations, i], V[n_simulations:2*n_simulations, i+1],
                                                Z[0,:,i], -Z[1, :,i+1], K_1, K_2, K_3, K_4, rdtK0, nu, sigma)

# ------------------------------------------------- '-+'       
        inx, m, s_2 = kernel_tg_1(V[2*n_simulations:3*n_simulations, i], E, p1, p2, p3, dx)
        
        nu           = m * f_nu_grid[inx]
        sigma        = cp.sqrt(s_2)*f_sigma_grid[inx]

        logS[2*n_simulations:3*n_simulations,i+1], V[2*n_simulations:3*n_simulations, i+1] = kernel_tg_2(logS[2*n_simulations:3*n_simulations,i], V[2*n_simulations:3*n_simulations, i], V[2*n_simulations:3*n_simulations, i+1],
                                                -Z[0,:,i], Z[1, :,i+1], K_1, K_2, K_3, K_4, rdtK0, nu, sigma)
        
# ------------------------------------------------- '--'
        inx, m, s_2 = kernel_tg_1(V[3*n_simulations:4*n_simulations, i], E, p1, p2, p3, dx)
        
        nu           = m * f_nu_grid[inx]
        sigma        = cp.sqrt(s_2)*f_sigma_grid[inx]

        logS[3*n_simulations:4*n_simulations,i+1], V[3*n_simulations:4*n_simulations, i+1] = kernel_tg_2(logS[3*n_simulations:4*n_simulations,i], V[3*n_simulations:4*n_simulations, i], V[3*n_simulations:4*n_simulations, i+1],
                                                -Z[0,:,i], -Z[1, :,i+1], K_1, K_2, K_3, K_4, rdtK0, nu, sigma)
                                                

    return [cp.exp(logS[:, N_T-1]), V[:, N_T-1]]