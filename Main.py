__author__ = 'dgevans'
from parameters import parameters
import numpy as np
import bellman
import initialize
import LucasStockey as LS
from parameters import UCES_AMSS
from parameters import UQL

    


def compareI(x,c_policy,Para):
    cLS,lLS,ILS = LS.solveLucasStockey_alt(x,Para)
    cSB = np.zeros(S)
    for s in range(0,S):
        cSB[s] = c_policy[(0,s)](x)
    lSB = (cSB+Para.g)/Para.theta
    ISB =  Para.I(cSB,lSB)
    return ILS-ISB

Para = parameters()
Para.g = [.15,.17]
S = len(Para.g)
Para.P = np.ones((S,S))/S
#Para.U = UCES_AMSS
#Para.P = np.array([[.6,.4],[.4,.6]])
Para.beta = np.array([.93,.97])
Para.sigma = 1
Para.sigma_1 = 1
Para.sigma_2 = 1
Para.eta = 2.258
Para.nx = 200
S = Para.P.shape[0]
Para.xmax = 3.0
Para.xmin = -2.0
Para.transfers = False


##Setup grid and initialize value function
#Setup
Para = initialize.setupGrid(Para)
Para.bounds = [(0,10)]*S+[(Para.xmin,Para.xmax)]*S
Vf,c_policy,xprime_policy = initialize.initializeFunctions(Para)

#Iterate until convergence
coef_old = np.zeros((Para.nx,S))
for s in range(0,S):
    coef_old[:,s] = Vf[s].getCoeffs()

Nmax = 200

diff = []
for i in range(0,Nmax):
    Vf,c_policy,xprime_policy = bellman.iterateBellmanLocally(Vf,c_policy,xprime_policy,Para)
    diff.append(0)
    for s_ in range(0,S):
        diff[i] = max(diff[i],np.max(np.abs(coef_old[:,s_]-Vf[s_].getCoeffs())))
        coef_old[:,s_] = Vf[s_].getCoeffs()
    print diff[i]

#Now fit accurate Policy functions
nx = min(Para.nx*10,1000)
xgrid = np.linspace(Para.xmin,Para.xmax,nx)
c_policy,xprime_policy = bellman.fitNewPolicies(xgrid,Vf,c_policy,xprime_policy,Para)

