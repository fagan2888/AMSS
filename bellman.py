__author__ = 'dgevans'
import numpy as np
from Spline import Spline
from scipy.optimize import fmin_slsqp
from parameters import DictWrap
import multiprocessing
from multiprocessing import Pool
from functools import partial
import itertools
from mpi4py import MPI
import sys

#Holds value function truly it actually fits the certain consumption equivalent as that will be closer to linear
class ValueFunctionSpline:
    def __init__(self,X,y,k,sigma,beta):
        self.sigma = sigma
        self.beta = beta
        if sigma == 1.0:
            y = np.exp((1-beta)*y)
        else:
            y = ((1-beta)*(1.0-sigma)*y)**(1.0/(1.0-sigma))
        self.f = Spline(X,y,k)

    def fit(self,X,y,k):
        if self.sigma == 1.0:
            y = np.exp((1-self.beta)*y)
        else:
            y = ((1-self.beta)*(1.0-self.sigma)*y)**(1.0/(1.0-self.sigma))
        self.f.fit(X,y,k)

    def getCoeffs(self):
        return self.f.getCoeffs()

    def __call__(self,X,d = None):
        if d==None:
            if self.sigma == 1.0:
                return np.log(self.f(X,d))/(1-self.beta)
            else:
                return (self.f(X,d)**(1.0-self.sigma))/((1.0-self.sigma)*(1-self.beta))

        if d==1:
            return self.f(X)**(-self.sigma) * self.f(X,1)/(1-self.beta)
        raise Exception('Error: d must equal None or 1')


#for a given state x and s_ solves value function problem. returns polcies and objective
def findPolicies(x_s_,Vf,c_policy,xprime_policy,Para,z0=None):
    S = Para.P.shape[0]
    bounds = Para.bounds
    x = x_s_[0]
    s_ = x_s_[1]
    state = DictWrap({'x': x,'s':s_})
    if z0 ==None:
        z0 = np.zeros(2*S)
        for s in range(0,S):
            z0[s] = c_policy[(s_,s)](x)
            z0[S+s] = xprime_policy[(s_,s)](x)
    if Para.transfers == False:
        (policy,minusv,_,imode,smode) = fmin_slsqp(objectiveFunction,z0,f_eqcons=impCon,bounds=bounds,fprime=objectiveFunctionJac,fprime_eqcons=impConJac,args=(Vf,Para,state),iprint=False,full_output=True,acc=1e-8,iter=1000)
    else:
        (policy,minusv,_,imode,smode) = fmin_slsqp(objectiveFunction,z0,f_ieqcons=impCon,bounds=bounds,fprime=objectiveFunctionJac,fprime_ieqcons=impConJac,args=(Vf,Para,state),iprint=False,full_output=True,acc=1e-8,iter=1000)
    if imode != 0:
        print x_s_
        raise Exception(smode)
    return policy[0:S],policy[S:2*S],-minusv

def findPoliciesOnGrid(x_s_grid,Vf,c_policy,xprime_policy,Para,ncpu=None):
    """
    Solves for the optimal policies on the grid of states x_s_grid.  Will use muliple cores locally if desired

    """
    findPolicies_partial = partial(findPolicies,Vf=Vf,c_policy=c_policy,xprime_policy=xprime_policy,Para=Para)
    if ncpu==None:
        ncpu = multiprocessing.cpu_count()
    if ncpu>1:
        p = Pool(ncpu)
        return p.map(findPolicies_partial,x_s_grid,len(x_s_grid)/ncpu)
    else:
        return map(findPolicies_partial,x_s_grid)


def iterateBellmanLocally(Vf,c_policy,xprime_policy,Para):
    """
     Iterates the bellman equation locally returns policy function Vf,c_policy,xprime_policy
    """
    policies = findPoliciesOnGrid(Para.domain,Vf,c_policy,xprime_policy,Para,ncpu=1)

    return fitPolicies(policies,Vf,c_policy,xprime_policy,Para)

def iterateBellmanOnCloud(Vf,c_policy,xprime_policy,Para,nCloud = 10):
    """
    Iterates Bellman equation on cloud, return policy functions Vf,c_policy,xprime_policy
    """
    nxs = len(Para.domain)
    n = nxs/nCloud
    domain_chunks = [Para.domain[i:i+n] for i in range(0,nxs,n)] #Break Para.domain into chunks to work on in cloud

    #solve optimization on cloud
    jids = cloud.map(lambda x_s_grid: findPoliciesOnGrid(x_s_grid,Vf,c_policy,xprime_policy,Para,ncpu=2)
        ,domain_chunks,_env='gspy_env',_type='f2')
    chunked_policies = cloud.result(jids)
    policies = list(itertools.chain.from_iterable(chunked_policies)) #collapse polcies into one list

    return fitPolicies(policies,Vf,c_policy,xprime_policy,Para) #fit policy functions from policy list

def iterateBellmanMPI(Vf,c_policy,xprime_policy,Para):

    w = MPI.COMM_WORLD
    s = w.Get_size()
    rank = w.Get_rank()
    n = len(Para.domain)
    m = n/s
    r = n%s

    mydomain = Para.domain[rank*m+min(rank,r):(rank+1)*m+min(rank+1,r)]
    findPolicies_partial = partial(findPolicies,Vf=Vf,c_policy=c_policy,xprime_policy=xprime_policy,Para=Para)
    mypolicies = map(findPolicies_partial,mydomain)

    chunked_policies = w.gather(mypolicies,root=0) #gather all the policies at master
    if rank == 0:
        policies = list(itertools.chain.from_iterable(chunked_policies))
        policyFunctions = fitPolicies(policies,Vf,c_policy,xprime_policy,Para) #have master fit policy functions
    else:
        policyFunctions = []

    return w.bcast(policyFunctions,root=0) #send the fit back to rest of group



def fitPolicies(policies,Vf,c_policy,xprime_policy,Para):
    """
    Given the new policies fits a new value function and policy functions.
    """
    S = Para.P.shape[0]
    policies = [policies[i:i+Para.nx] for i in range(0,len(policies),Para.nx)] #split policies up into groups by S
    for s_ in range(0,S):
        [c_new,xprime_new,V_new] = zip(*policies[s_]) #unzip the list of tuples into the c,xprime policies and associated values
        Vf[s_].fit(Para.xgrid,np.hstack(V_new)[:],[1])
        for s in range(0,S):
            c_policy[(s_,s)].fit(Para.xgrid,np.vstack(c_new)[:,s],[1]) #vstack is used here because c_new is really a list of arrays
            xprime_policy[(s_,s)].fit(Para.xgrid,np.vstack(xprime_new)[:,s],[1])

    return Vf,c_policy,xprime_policy

def objectiveFunction(z,V,Para,state):
    """
    Computes the objective function to be optimized.  z is [c,xprime]
    """
    u = Para.U.u
    P = Para.P

    S = P.shape[0]

    c = z[0:S]
    l = (c+Para.g)/Para.theta
    xprime = z[S:2*S]
    Vprime = np.zeros(S)

    for s in range(0,S):
        Vprime[s] = V[s](xprime[s])

    return -np.dot(P[state.s,:], u(c,l,Para) + Para.beta*Vprime )

def objectiveFunctionJac(z,V,Para,state):
    """
    Computes the derivative of the objective function. z is [c,xprime]
    """
    P = Para.P

    S = P.shape[0]

    c = z[0:S]
    l = (c+Para.g)/Para.theta
    xprime = z[S:2*S]
    dVprime = np.zeros(S)
    uc = Para.U.uc(c,l,Para)
    ul = Para.U.ul(c,l,Para)

    for s in range(0,S):
        dVprime[s] = V[s](xprime[s],1)#the ,1 indicates a first derivative

    return np.hstack((-P[state.s,:]*( uc+ul/Para.theta ),#derivative w.r.t. c
                       -P[state.s,:]*Para.beta*dVprime))#derivative w.r.t xprime

def impCon(z,V,Para,state):
    """
    Computes the implementability constraint.
    """
    x = state.x
    s_ = state.s
    P = Para.P
    S = Para.P.shape[0]
    if Para.port == None:
        p = np.ones(S)
    else:
        p = Para.port
    beta = Para.beta

    c = z[0:S]
    l = (c+Para.g)/Para.theta
    xprime =z[S:2*S]
    uc = Para.U.uc(c,l,Para)
    ul = Para.U.ul(c,l,Para)


    Euc = np.dot(P[s_,:],uc*p)

    return c*uc + l*ul + beta*xprime - x*uc*p/(Euc)

def impConJac(z,V,Para,state):
    """
    Computes the Jacobian of the implementability constraint
    """
    x = state.x
    s_ = state.s
    P = Para.P
    S = Para.P.shape[0]
    if Para.port == None:
        p = np.ones(S)
    else:
        p = Para.port
    beta = Para.beta
    theta = Para.theta

    c = z[0:S]
    l = (c+Para.g)/theta
    uc = Para.U.uc(c,l,Para)
    ul = Para.U.ul(c,l,Para)
    ucc = Para.U.ucc(c,l,Para)
    ull = Para.U.ull(c,l,Para)
    Euc = np.dot(P[s_,:],uc*p)

    JacI = np.diag( uc+ucc*c+(ul+ull*l)/theta ) #derivative of I = uc*c+ul*l w.r.t c
    JacXprime = np.diag(beta*np.ones(S))  #derivative of xprime w.r.t xprime
    #derivative of -x*uc/(beta Euc) w.r.t. c
    JacXterm = np.diag(-x*ucc*p/(Euc)) + x*np.kron((uc*p).reshape(S,1),P[s_,:]*(ucc*p).reshape(1,S))/(Euc**2)
    return np.hstack((JacI+JacXterm #derivative w.r.t c
                      ,JacXprime)) #derivative w.r.t xprime


def simulate(x0,T,xprime_policy,Para):
    """
    Simulates starting from x0 given xprime_policy for T periods.  Returns sequence of xprimes and shocks
    """
    S = Para.P.shape[0]
    xHist = np.zeros(T)
    sHist = np.zeros(T,dtype=np.int)
    xHist[0] = x0
    cumP = np.cumsum(Para.P,axis=1)
    for t in range(1,T):
        r = np.random.uniform()
        s_ = sHist[t-1]
        for s in range(0,S):
            if r < cumP[s_,s]:
                sHist[t] = s
                break
        xHist[t] = xprime_policy[(s_,s)](xHist[t-1])

    return xHist,sHist

def fitNewPolicies(xgrid,Vf,c_policy,xprime_policy,Para):
    """
    Fits new policies locally on a larger xgrid
    """
    S = Para.P.shape[0]
    Para.xgrid = xgrid
    Para.nx = xgrid.shape[0]
    xDomain = np.kron(np.ones(S),Para.xgrid) #stack Para.xgrid S times
    s_Domain = np.kron(range(0,S),np.ones(Para.nx,dtype=np.int)) #s assciated with each grid
    Para.domain = zip(xDomain,s_Domain)#zip them together so we have something that looks like
    _,c_policy,xprime_policy =iterateBellmanMPI(Vf,c_policy,xprime_policy,Para)
    return c_policy,xprime_policy
    



