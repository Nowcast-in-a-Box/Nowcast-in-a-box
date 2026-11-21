package handler

import "github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler/internal/resources"

type (
	ResourceBuild       = resources.ResourceBuild
	ResourceMount       = resources.ResourceMount
	ResourceContainer   = resources.ResourceContainer
	VerificationSpec    = resources.VerificationSpec
	DataResource        = resources.DataResource
	ModelWeights        = resources.ModelWeights
	ModelResource       = resources.ModelResource
	DiagnosticsResource = resources.DiagnosticsResource
	Resources           = resources.Resources
)

func LoadResources(root string) (*Resources, error) { return resources.LoadResources(root) }
